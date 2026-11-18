"""SQL-backed end-to-end provider recovery tests without network or Snowflake.

Runs the actual adapter parsers, source selection, candidate persistence and
promotion against a real SQLite SQL engine with a narrow SQL dialect adapter.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal

from ingestion.gpw_multi_source_job import run_job
from ingestion.gpw_provider_feeds import BiznesRadarFeed, RawCandle, StooqFeed

DAY = date(2026, 11, 17)


class Cursor:
    def __init__(self, real: sqlite3.Cursor) -> None:
        self.real = real

    def execute(self, sql: str, params=()) -> "Cursor":
        sql = sql.replace("%s", "?")
        sql = re.sub(r'TO_DATE\("DATE"\)', 'DATE("DATE")', sql)
        sql = re.sub(r"TO_DATE\(\?,\s*'YYYY-MM-DD'\)", "DATE(?)", sql)
        sql = re.sub(r"TO_TIMESTAMP_NTZ\(\?,\s*'YYYY-MM-DD'\)", "?", sql)
        sql = re.sub(r"TO_DECIMAL\(\?,\s*\d+,\s*\d+\)", "?", sql)
        sql = sql.replace("CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP")
        self.real.execute(sql, params)
        return self

    def fetchall(self):
        return self.real.fetchall()

    @property
    def rowcount(self) -> int:
        return self.real.rowcount

    def close(self) -> None:
        self.real.close()


class Database:
    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript("""
            CREATE TABLE SECURITIES (ASSET_ID INTEGER PRIMARY KEY, SYMBOL TEXT,
                VENUE TEXT, INSTRUMENT_CLASS TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE PROVIDERS (PROVIDER_ID INTEGER PRIMARY KEY,
                PROVIDER_CODE TEXT, PRIORITY INTEGER, IS_ACTIVE BOOLEAN);
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
        self.con.execute('INSERT INTO SECURITIES VALUES (101,"PZU.WA","GPW","stock",1)')
        self.con.executemany(
            "INSERT INTO PROVIDERS VALUES (?,?,?,1)",
            [(1, "YFINANCE", 10), (2, "STOOQ", 20), (3, "BIZNESRADAR", 30)],
        )
        self.con.executemany(
            "INSERT INTO PROVIDER_SYMBOLS VALUES (?,?,?,?,?,NULL,1)",
            [
                (1, 101, 1, "PZU.WA", "1900-01-01"),
                (2, 101, 2, "pzu", "1900-01-01"),
                (3, 101, 3, "PZU", "1900-01-01"),
            ],
        )

    def cursor(self) -> Cursor:
        return Cursor(self.con.cursor())


class HTTPResponse:
    def __init__(self, content: str) -> None:
        self.text = content

    def raise_for_status(self) -> None:
        return None


class HTTP:
    def __init__(self, body: str) -> None:
        self.body = body

    def get(self, *args, **kwargs) -> HTTPResponse:
        return HTTPResponse(self.body)


class Yahoo:
    code = "YFINANCE"

    def __init__(self, volume: int) -> None:
        self.volume = volume

    def history(self, symbol, start, end):
        return [
            RawCandle(
                DAY,
                Decimal("75.96"),
                Decimal("75.96"),
                Decimal("74.78"),
                Decimal("75.00"),
                self.volume,
            )
        ]


def make_feeds(*, stooq_error: bool = False, yahoo_volume: int = 0):
    csv = (
        "Download CSV needs API key"
        if stooq_error
        else "Date,Open,High,Low,Close,Volume\n2026-11-17,75.96,75.96,74.78,75.00,1247844\n"
    )
    html = """<html><h1>Notowania historyczne PZU</h1><p>GPW - Akcje</p>
      <table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th>
      <th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr>
      <tr><td>17.11.2026</td><td>75,96</td><td>75,96</td><td>74,78</td>
      <td>75,00</td><td>1 247 844</td><td>1000000</td></tr></table></html>"""
    return {
        "YFINANCE": Yahoo(yahoo_volume),
        "STOOQ": StooqFeed(session=HTTP(csv)),
        "BIZNESRADAR": BiznesRadarFeed(session=HTTP(html)),
    }


def _run(db: Database, feeds, *, write=True):
    return run_job(
        db, feeds=feeds, symbols=["PZU.WA"], start=DAY, end=DAY, write=write, request_delay=0
    )


def test_three_source_real_parsers_repair_pzu_and_rerun_idempotent():
    db = Database()
    db.con.execute(
        "INSERT INTO PRICES (SYMBOL,DATE,ASSET_ID,OPEN,HIGH,LOW,CLOSE,VOLUME) "
        'VALUES ("PZU.WA","2026-11-17",101,75.96,75.96,74.78,75,0)'
    )
    result = _run(db, make_feeds())
    assert result.promoted == 1 and result.candidates_inserted == 3
    assert result.unresolved == 0 and not result.provider_errors
    assert db.con.execute("SELECT VOLUME,PROVIDER_ID FROM PRICES").fetchone() == (1247844, 2)
    assert db.con.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (3,)
    before = db.con.execute("SELECT * FROM PRICES").fetchall()
    replay = _run(db, make_feeds())
    assert replay.candidates_inserted == 0
    assert db.con.execute("SELECT * FROM PRICES").fetchall() == before
    db.con.close()


def test_degraded_stooq_can_still_promote_two_valid_independent_sources():
    db = Database()
    result = _run(db, make_feeds(stooq_error=True, yahoo_volume=1247844))
    assert result.promoted == 1
    assert result.candidates_inserted == 2
    assert len(result.provider_errors) == 1
    assert "STOOQ" in result.provider_errors[0]
    assert db.con.execute("SELECT COUNT(*) FROM PRICES").fetchone() == (1,)
    db.con.close()


def test_one_valid_provider_promotes_even_if_other_sources_fail():
    db = Database()
    result = _run(db, make_feeds(stooq_error=True, yahoo_volume=0))
    assert result.promoted == 1 and result.unresolved == 0
    assert db.con.execute("SELECT PROVIDER_ID,SELECTION_REASON FROM PRICES").fetchone() == (
        3,
        "SINGLE_VALID_SOURCE",
    )
    assert db.con.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    db.con.close()


def test_strict_consensus_opt_in_remains_available(caplog):
    """Whole BiznesRadar candle selected; rejected Yahoo confirms prices only."""
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)
    caplog.set_level("WARNING")
    strict = run_job(
        db,
        feeds=feeds,
        symbols=["PZU.WA"],
        start=DAY,
        end=DAY,
        write=True,
        request_delay=0,
        minimum_sources=2,
    )
    assert strict.promoted == 0 and strict.unresolved == 1
    assert db.con.execute("SELECT COUNT(*) FROM PRICES").fetchone() == (0,)
    assert "ZERO_VOLUME_LIQUID_EQUITY" in caplog.text

    fallback = run_job(
        db,
        feeds=feeds,
        symbols=["PZU.WA"],
        start=DAY,
        end=DAY,
        write=True,
        request_delay=0,
    )
    assert fallback.promoted == 1 and fallback.unresolved == 0
    assert fallback.candidates_inserted == 0  # candidate IDs are replay-stable
    assert db.con.execute(
        "SELECT VOLUME, PROVIDER_ID, SELECTION_REASON FROM PRICES"
    ).fetchone() == (
        1247844,
        3,
        "SINGLE_VALID_SOURCE",
    )
    rows = db.con.execute(
        "SELECT VALIDATION_STATUS, VALIDATION_REASON FROM PRICES_CANDIDATES ORDER BY PROVIDER_ID"
    ).fetchall()
    assert rows == [("REJECTED", "ZERO_VOLUME_LIQUID_EQUITY"), ("VALID", None)]
    before = db.con.execute("SELECT * FROM PRICES").fetchall()
    replay = run_job(
        db, feeds=feeds, symbols=["PZU.WA"], start=DAY, end=DAY, write=True, request_delay=0
    )
    assert replay.candidates_inserted == 0
    assert db.con.execute("SELECT * FROM PRICES").fetchall() == before
    db.con.close()


def test_disagreeing_rejected_yahoo_does_not_block_valid_biznesradar():
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)

    class DisagreeingYahoo:
        code = "YFINANCE"

        def history(self, _symbol, _start, _end):
            return [
                RawCandle(
                    DAY, Decimal("80.00"), Decimal("80.00"), Decimal("79.00"), Decimal("79.50"), 0
                )
            ]

    feeds["YFINANCE"] = DisagreeingYahoo()
    result = run_job(
        db, feeds=feeds, symbols=["PZU.WA"], start=DAY, end=DAY, write=True, request_delay=0
    )
    assert result.promoted == 1 and result.unresolved == 0
    assert db.con.execute("SELECT PROVIDER_ID, VOLUME FROM PRICES").fetchone() == (3, 1247844)
    assert db.con.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    db.con.close()


def test_two_zero_volume_sources_never_create_authoritative_candle():
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)

    class BiznesRadarZero:
        code = "BIZNESRADAR"

        def history(self, _symbol, _start, _end):
            return [
                RawCandle(
                    DAY, Decimal("75.96"), Decimal("75.96"), Decimal("74.78"), Decimal("75.00"), 0
                )
            ]

    feeds["BIZNESRADAR"] = BiznesRadarZero()
    result = run_job(
        db, feeds=feeds, symbols=["PZU.WA"], start=DAY, end=DAY, write=True, request_delay=0
    )
    assert result.promoted == 0 and result.unresolved == 1
    assert db.con.execute("SELECT COUNT(*) FROM PRICES").fetchone() == (0,)
    assert db.con.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    db.con.close()
