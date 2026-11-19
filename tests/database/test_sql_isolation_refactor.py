"""SQL boundary regression tests; no live Snowflake credentials needed."""

from __future__ import annotations

import ast
import pandas as pd
import re
from datetime import date
from pathlib import Path
import sqlite3

import pytest

from database.catalog_queries import (
    derivative_candidate_review_rows,
    scheduled_symbols,
    stored_fx_dates,
)
from database.gap_queries import session_coverage
from database.index_queries import index_prices_table_exists
from database.market_metadata import eligible_derivative_contracts


class Cursor:
    def __init__(self, inner):
        self.inner = inner

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        sql = sql.replace('CAST("DATE" AS DATE)', 'DATE("DATE")')
        if "INFORMATION_SCHEMA.TABLES" in sql:
            sql = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND UPPER(name)=?"
        return self.inner.execute(
            sql, tuple(item.isoformat() if isinstance(item, date) else item for item in params)
        )

    def fetchall(self):
        return self.inner.fetchall()

    def fetchone(self):
        return self.inner.fetchone()

    def close(self):
        self.inner.close()


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript("""
            CREATE TABLE INDEX_PRICES (INDEX_SYMBOL TEXT, DATE DATE, CLOSE DECIMAL);
            CREATE TABLE PRICES (SYMBOL TEXT, DATE DATE, CLOSE DECIMAL);
            CREATE TABLE DERIVATIVE_CONTRACTS (CONTRACT_ID TEXT, INSTRUMENT_TYPE TEXT, EXPIRY_DATE DATE);
            CREATE TABLE DERIVATIVE_CANDIDATES (SYMBOL TEXT, INSTRUMENT_TYPE TEXT, EXPIRY_DATE DATE);
            CREATE TABLE SECURITIES (SYMBOL TEXT, VENUE TEXT);
            CREATE TABLE CURRENCY_PAIRS (SYMBOL TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE FX_RATES (CURRENCY_PAIR_ID INTEGER, DATE DATE);
        """)

    def cursor(self):
        return Cursor(self.db.cursor())


def test_gap_coverage_with_real_sqlite_database():
    conn = Connection()
    conn.db.executemany(
        "INSERT INTO INDEX_PRICES VALUES ('WIG20',?,100)",
        [("2026-11-13",), ("2026-11-17",), ("2026-11-18",)],
    )
    extent, observed = session_coverage(
        conn,
        dataset="WIG20",
        symbols=["WIG20"],
        start=date(2026, 11, 13),
        end=date(2026, 11, 18),
        backend="duckdb",
    )
    assert extent == {"WIG20": (date(2026, 11, 13), date(2026, 11, 18))}
    assert date(2026, 11, 16) not in observed["WIG20"]
    assert date(2026, 11, 17) in observed["WIG20"]
    with pytest.raises(ValueError, match="Unsupported"):
        session_coverage(
            conn,
            dataset="FX",
            symbols=["EURPLN=X"],
            start=date(2026, 11, 13),
            end=date(2026, 11, 18),
        )
    conn.db.close()


def test_catalog_and_derivative_discovery_use_parameterized_queries():
    conn = Connection()
    conn.db.execute("INSERT INTO SECURITIES VALUES ('PZU.WA','GPW')")
    conn.db.execute("INSERT INTO SECURITIES VALUES ('AAPL','US')")
    conn.db.execute("INSERT INTO CURRENCY_PAIRS VALUES ('EURPLN=X',1)")
    conn.db.execute("INSERT INTO DERIVATIVE_CONTRACTS VALUES ('FW20Z2620','FUTURE','2026-12-18')")
    conn.db.execute("INSERT INTO DERIVATIVE_CONTRACTS VALUES ('BAD','OPTION','2026-01-01')")
    conn.db.execute("INSERT INTO DERIVATIVE_CANDIDATES VALUES ('FW20Z2620','FUTURE','2026-12-18')")
    conn.db.execute("INSERT INTO FX_RATES VALUES (17,'2026-11-17')")
    assert scheduled_symbols(conn, "GPW", backend="duckdb") == ["PZU.WA"]
    assert scheduled_symbols(conn, "FX", backend="duckdb") == ["EURPLN=X"]
    assert eligible_derivative_contracts(conn, since=date(2026, 11, 16), backend="duckdb") == [
        "FW20Z2620"
    ]
    assert derivative_candidate_review_rows(conn) == [("FW20Z2620", "FUTURE", "2026-12-18")]
    assert stored_fx_dates(
        conn, pair_id=17, start=date(2026, 11, 16), end=date(2026, 11, 18), backend="duckdb"
    ) == {date(2026, 11, 17)}
    conn.db.close()


def test_index_table_guard_uses_backend_catalog():
    conn = Connection()
    assert index_prices_table_exists(conn, backend="duckdb")
    with pytest.raises(ValueError, match="Unsupported"):
        index_prices_table_exists(conn, backend="mysql")


def test_sql_is_gone_from_refactored_callers():
    # The test lives in tests/database/ and resolves the project root independently.
    root = Path(__file__).resolve().parents[2]
    paths = (
        "ikaros_app/gap_repair.py",
        "ingestion/index_prices_refresh.py",
        "ingestion/wig20_stooq.py",
        "ingestion/kafka_snowflake_sink.py",
        "run/ingest_market_prices.py",
        "run/refresh_index_prices.py",
        "run/iic_status.py",
        "run/review_gpw_contracts.py",
        "run/check_snowflake_tables.py",
        "run/smoke_fx_snowflake.py",
        "run/ikaros_scheduler.py",
    )
    for relative in paths:
        module = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper()
                assert not re.search(
                    r"(?:MERGE\s+INTO|INSERT\s+INTO|DELETE\s+FROM|"
                    r'CREATE\s+TABLE|UPDATE\s+)\s*["\x27`]|'
                    r'SELECT\s+["\x27`][A-Z_]+',
                    upper,
                ), f"SQL found outside database/: {relative}"


class RecordingCursor:
    def __init__(self, connection):
        self.conn = connection
        self.rowcount = 1

    def execute(self, sql, params=(), **kwargs):
        self.conn.calls.append((sql, params or kwargs.get("params", ())))
        if self.conn.fail and "MERGE INTO" in sql:
            raise RuntimeError("simulated write failure")
        return self

    def fetchall(self):
        return self.conn.rows

    def fetchone(self):
        return self.conn.rows[0] if self.conn.rows else None

    def close(self):
        return None


class RecordingConnection:
    def __init__(self, *, rows=(), fail=False):
        self.calls = []
        self.rows = list(rows)
        self.fail = fail

    def cursor(self):
        return RecordingCursor(self)


def test_yahoo_index_writer_preserves_single_transaction_and_source_filter():
    from database.index_queries import upsert_index_prices

    conn = RecordingConnection()
    frame = pd.DataFrame(
        [
            {"date": date(2026, 11, 17), "open": 2800, "high": 2810, "low": 2790, "close": 2805},
        ]
    )
    upsert_index_prices(conn, frame)
    calls = conn.calls
    assert calls[0][0] == "BEGIN"
    assert 'MERGE INTO "INDEX_PRICES"' in calls[1][0]
    assert "WHEN MATCHED AND t.\"SOURCE\" LIKE 'yfinance|%%'" in calls[1][0]
    assert calls[1][1] == (
        "WIG20",
        "2026-11-17",
        2800.0,
        2810.0,
        2790.0,
        2805.0,
        "yfinance|WIG20.WA",
    )
    assert calls[-1][0] == "COMMIT"


def test_yahoo_index_writer_rolls_back_failed_merge():
    from database.index_queries import upsert_index_prices

    conn = RecordingConnection(fail=True)
    frame = pd.DataFrame(
        [
            {"date": date(2026, 11, 17), "open": 2800, "high": 2810, "low": 2790, "close": 2805},
        ]
    )
    with pytest.raises(RuntimeError, match="simulated"):
        upsert_index_prices(conn, frame)
    assert conn.calls[-1][0] == "ROLLBACK"
    assert not any(sql == "COMMIT" for sql, _ in conn.calls)


def test_kafka_fx_pair_lookup_validates_exactly_one_active_mapping():
    from database.kafka_sink_queries import pair_id

    assert pair_id(RecordingConnection(rows=[(17,)]), "EURPLN=X") == 17
    with pytest.raises(ValueError, match="Expected one active"):
        pair_id(RecordingConnection(rows=[]), "EURPLN=X")
    with pytest.raises(ValueError, match="Expected one active"):
        pair_id(RecordingConnection(rows=[(17,), (18,)]), "EURPLN=X")


def test_kafka_gpw_dml_stays_inside_database_module():
    from datetime import date
    from types import SimpleNamespace
    from database.kafka_sink_queries import upsert_observation

    event = SimpleNamespace(
        dataset="GPW",
        symbol="PZU.WA",
        day=date(2026, 11, 17),
        open=75.96,
        high=75.96,
        low=74.78,
        close=75.00,
        volume=1248008,
        dividend=None,
        split_ratio=None,
    )
    conn = RecordingConnection()
    upsert_observation(conn, event)
    assert len(conn.calls) == 1
    assert 'MERGE INTO "PRICES"' in conn.calls[0][0]
    assert conn.calls[0][1] == (
        "PZU.WA",
        "2026-11-17",
        75.96,
        75.96,
        74.78,
        75.0,
        1248008,
        None,
        None,
    )
