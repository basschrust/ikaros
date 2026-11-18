"""End-to-end SQL-backed tests using an in-memory DB-API adapter.

No real Snowflake account is needed. Production Snowflake SQL is translated
only at the boundary to SQLite equivalents; all tables are real SQL tables.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from database.gpw_price_pipeline import load_mappings
from ingestion.gpw_multi_source_job import run_job
from ingestion.gpw_provider_feeds import RawCandle

DAY = date(2026, 11, 17)


class Cursor:
    def __init__(self, cur):
        self.inner = cur

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(r"TO_DATE\(\"DATE\"\)", 'DATE("DATE")', sql)
        sql = re.sub(r"TO_DATE\(\?,\s*'YYYY-MM-DD'\)", "DATE(?)", sql)
        sql = re.sub(r"TO_TIMESTAMP_NTZ\(\?,\s*'YYYY-MM-DD'\)", "?", sql)
        sql = re.sub(r"TO_DECIMAL\(\?,\s*\d+,\s*\d+\)", "?", sql)
        sql = sql.replace("CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP")
        self.inner.execute(sql, params)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    @property
    def rowcount(self):
        return self.inner.rowcount

    def close(self):
        self.inner.close()


class DB:
    def __init__(self, *, existing_volume=0):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE SECURITIES (ASSET_ID INTEGER PRIMARY KEY, SYMBOL TEXT, VENUE TEXT,
                INSTRUMENT_CLASS TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE PROVIDERS (PROVIDER_ID INTEGER PRIMARY KEY, PROVIDER_CODE TEXT,
                PRIORITY INTEGER, IS_ACTIVE BOOLEAN);
            CREATE TABLE PROVIDER_SYMBOLS (PROVIDER_SYMBOL_ID INTEGER PRIMARY KEY,
                ASSET_ID INTEGER, PROVIDER_ID INTEGER, PROVIDER_SYMBOL TEXT,
                VALID_FROM TEXT, VALID_TO TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE PRICES_CANDIDATES (CANDIDATE_ID TEXT PRIMARY KEY,
                ASSET_ID INTEGER, PROVIDER_ID INTEGER, PROVIDER_SYMBOL_ID INTEGER,
                SYMBOL TEXT, DATE TEXT, OPEN DECIMAL, HIGH DECIMAL, LOW DECIMAL,
                CLOSE DECIMAL, VOLUME DECIMAL, VALIDATION_STATUS TEXT,
                VALIDATION_REASON TEXT);
            CREATE TABLE PRICES (SYMBOL TEXT, DATE TEXT, ASSET_ID INTEGER,
                OPEN DECIMAL, HIGH DECIMAL, LOW DECIMAL, CLOSE DECIMAL,
                VOLUME DECIMAL, PROVIDER_ID INTEGER, CANDIDATE_ID TEXT,
                SELECTED_AT TEXT, SELECTION_REASON TEXT);
        """)
        self.db.execute('INSERT INTO SECURITIES VALUES (101,"PZU.WA","GPW","stock",1)')
        self.db.executemany(
            "INSERT INTO PROVIDERS VALUES (?,?,?,1)",
            [(1, "YFINANCE", 10), (2, "STOOQ", 20), (3, "BIZNESRADAR", 30)],
        )
        self.db.executemany(
            "INSERT INTO PROVIDER_SYMBOLS VALUES (?,?,?,?,?,NULL,1)",
            [
                (1, 101, 1, "PZU.WA", "1900-01-01"),
                (2, 101, 2, "pzu", "1900-01-01"),
                (3, 101, 3, "PZU", "1900-01-01"),
            ],
        )
        if existing_volume is not None:
            self.db.execute(
                "INSERT INTO PRICES (SYMBOL,DATE,ASSET_ID,OPEN,HIGH,LOW,CLOSE,VOLUME) "
                'VALUES ("PZU.WA","2026-11-17",101,75.96,75.96,74.78,75.0,?)',
                (existing_volume,),
            )

    def cursor(self):
        return Cursor(self.db.cursor())


class Feed:
    def __init__(self, code, volume, close="75.00"):
        self.code = code
        self.volume = volume
        self.close = close
        self.called = []

    def history(self, symbol, start, end):
        self.called.append((symbol, start, end))
        return [
            RawCandle(
                DAY,
                Decimal("75.96"),
                Decimal("75.96"),
                Decimal("74.78"),
                Decimal(self.close),
                self.volume,
            )
        ]


def feeds(stooq=1247844, br=1247844, yahoo=0):
    return {
        "YFINANCE": Feed("YFINANCE", yahoo),
        "STOOQ": Feed("STOOQ", stooq),
        "BIZNESRADAR": Feed("BIZNESRADAR", br),
    }


def run(db, source, *, write=True):
    return run_job(
        db, feeds=source, symbols=["PZU.WA"], start=DAY, end=DAY, write=write, request_delay=0
    )


def test_pzu_suspect_zero_volume_is_repaired_by_stooq_biznesradar():
    db = DB(existing_volume=0)
    src = feeds()
    result = run(db, src)
    assert result.promoted == 1 and result.candidates_inserted == 3
    assert not result.unresolved
    assert [
        x[0]
        for x in db.db.execute(
            "SELECT VALIDATION_STATUS FROM PRICES_CANDIDATES ORDER BY PROVIDER_ID"
        )
    ] == [
        "REJECTED",
        "VALID",
        "VALID",
    ]
    row = db.db.execute("SELECT ASSET_ID,PROVIDER_ID,VOLUME,CANDIDATE_ID FROM PRICES").fetchone()
    assert row[:3] == (101, 2, 1247844)
    assert len(row[3]) == 64
    assert src["STOOQ"].called[0][0] == "pzu"
    assert src["BIZNESRADAR"].called[0][0] == "PZU"


def test_second_run_does_not_add_candidates_or_modify_final_row():
    db = DB(existing_volume=None)
    first = run(db, feeds())
    assert first.promoted == 1
    snapshot = db.db.execute("SELECT * FROM PRICES").fetchall()
    again = run(db, feeds())
    assert again.candidates_inserted == 0
    assert db.db.execute("SELECT * FROM PRICES").fetchall() == snapshot
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3


def test_no_write_on_dry_run():
    db = DB(existing_volume=None)
    result = run(db, feeds(), write=False)
    assert result.days_seen == 1 and result.candidates_inserted == 0
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 0
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 0


def test_valid_existing_conflict_is_preserved_and_candles_staged():
    db = DB(existing_volume=5000000)
    result = run(db, feeds())
    assert result.preserved_conflicts == 1
    assert db.db.execute("SELECT VOLUME FROM PRICES").fetchone() == (5000000,)
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3


def test_valid_sources_disagree_priority_selects_whole_candle():
    db = DB(existing_volume=None)
    result = run(db, feeds(stooq=1247844, br=2000000))
    assert result.unresolved == 0 and result.promoted == 1
    assert db.db.execute("SELECT PROVIDER_ID,VOLUME,SELECTION_REASON FROM PRICES").fetchone() == (
        2,
        1247844,
        "PRIORITY_SELECTED_DISAGREEMENT",
    )
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3


def test_one_active_provider_mapping_can_promote():
    db = DB(existing_volume=None)
    db.db.execute('UPDATE PROVIDERS SET IS_ACTIVE=0 WHERE PROVIDER_CODE IN ("YFINANCE","STOOQ")')
    result = run(db, feeds())
    assert result.symbols_processed == 1 and result.promoted == 1
    assert result.provider_requests == 1
    assert db.db.execute("SELECT PROVIDER_ID FROM PRICES").fetchone() == (3,)


def test_missing_symbol_refuses_uncanonical_ingestion():
    db = DB(existing_volume=None)
    with pytest.raises(ValueError, match="Missing active GPW price mappings"):
        load_mappings(db, symbols=["UNKNOWN.WA"])


def test_missing_all_sources_is_not_reported_as_success():
    db = DB(existing_volume=None)
    source = feeds()
    for f in source.values():
        f.history = lambda *args: []
    result = run(db, source)
    assert result.days_seen == 0
    assert result.promoted == 0
    assert len(result.provider_errors) == 1
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 0
