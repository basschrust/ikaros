"""SQL-backed new-domain candidate ingestion, selection and replay tests."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from database.market_quote_pipeline import (
    ProviderMapping,
    choose,
    make_candidate,
    run_market_job,
    validate,
)
from ingestion.market_quote_feeds import MarketCandle, parse_index_history

DAY = date(2026, 11, 17)


class Cursor:
    def __init__(self, cursor):
        self.inner = cursor

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(
            r"\"(INDEX|DERIVATIVE)_PRICE_CANDIDATES_ID_SEQ\"\.NEXTVAL",
            lambda m: (
                f'(SELECT COALESCE(MAX("CANDIDATE_ID"),0)+1 FROM "{m.group(1)}_PRICE_CANDIDATES")'
            ),
            sql,
        )
        sql = re.sub(r'CAST\("DATE" AS DATE\)', 'DATE("DATE")', sql)
        sql = re.sub(r"CAST\(\? AS DATE\)", "DATE(?)", sql)
        self.inner.execute(sql, params)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    def close(self):
        self.inner.close()


class DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.executescript("""
            CREATE TABLE PROVIDERS (PROVIDER_ID INTEGER PRIMARY KEY, PROVIDER_CODE TEXT,
                PRIORITY INTEGER, IS_ACTIVE BOOLEAN);
            INSERT INTO PROVIDERS VALUES (1,'YFINANCE',10,1),(2,'BIZNESRADAR',30,1);
            CREATE TABLE INDEX_PROVIDER_SYMBOLS (
                PROVIDER_SYMBOL_ID INTEGER PRIMARY KEY, INDEX_SYMBOL TEXT, PROVIDER_ID INTEGER,
                PROVIDER_SYMBOL TEXT, VALID_FROM DATE, VALID_TO DATE, IS_ACTIVE BOOLEAN);
            INSERT INTO INDEX_PROVIDER_SYMBOLS VALUES
                (1,'WIG20',1,'WIG20.WA','2000-01-01',NULL,1),
                (2,'WIG20',2,'WIG20','2000-01-01',NULL,1);
            CREATE TABLE DERIVATIVE_PROVIDER_SYMBOLS (
                PROVIDER_SYMBOL_ID INTEGER PRIMARY KEY, CONTRACT_ID TEXT, PROVIDER_ID INTEGER,
                PROVIDER_SYMBOL TEXT, VALID_FROM DATE, VALID_TO DATE, IS_ACTIVE BOOLEAN);
            INSERT INTO DERIVATIVE_PROVIDER_SYMBOLS VALUES
                (3,'FW20Z2620',2,'FW20Z2620','2000-01-01',NULL,1);
            CREATE TABLE INDEX_PRICE_CANDIDATES (
                CANDIDATE_ID INTEGER PRIMARY KEY, CONTENT_HASH TEXT UNIQUE, INDEX_SYMBOL TEXT,
                PROVIDER_ID INTEGER, PROVIDER_SYMBOL_ID INTEGER, DATE DATE, OPEN NUMERIC,
                HIGH NUMERIC, LOW NUMERIC, CLOSE NUMERIC, VOLUME NUMERIC,
                SETTLEMENT NUMERIC, OPEN_INTEREST NUMERIC,
                VALIDATION_STATUS TEXT, VALIDATION_REASON TEXT);
            CREATE TABLE DERIVATIVE_PRICE_CANDIDATES (
                CANDIDATE_ID INTEGER PRIMARY KEY, CONTENT_HASH TEXT UNIQUE, CONTRACT_ID TEXT,
                PROVIDER_ID INTEGER, PROVIDER_SYMBOL_ID INTEGER, DATE DATE, OPEN NUMERIC,
                HIGH NUMERIC, LOW NUMERIC, CLOSE NUMERIC, VOLUME NUMERIC,
                SETTLEMENT NUMERIC, OPEN_INTEREST NUMERIC,
                VALIDATION_STATUS TEXT, VALIDATION_REASON TEXT);
            CREATE TABLE INDEX_PRICES (INDEX_SYMBOL TEXT, DATE DATE, OPEN NUMERIC,
                HIGH NUMERIC, LOW NUMERIC, CLOSE NUMERIC, SOURCE TEXT, CANDIDATE_ID INTEGER,
                PROVIDER_ID INTEGER, SELECTION_REASON TEXT, SELECTED_AT TEXT);
            CREATE TABLE DERIVATIVE_PRICES (CONTRACT_ID TEXT, DATE DATE, OPEN NUMERIC,
                HIGH NUMERIC, LOW NUMERIC, CLOSE NUMERIC, VOLUME NUMERIC,
                OPEN_INTEREST NUMERIC, SETTLEMENT NUMERIC, SOURCE TEXT,
                CANDIDATE_ID INTEGER, PROVIDER_ID INTEGER,
                SELECTION_REASON TEXT, SELECTED_AT TEXT);
        """)

    def cursor(self):
        return Cursor(self.conn.cursor())


class Feed:
    def __init__(self, code, close, volume=None):
        self.code = code
        self.quote = MarketCandle(
            DAY, Decimal("3200"), Decimal("3220"), Decimal("3190"), Decimal(close), volume
        )

    def history(self, symbol, start, end):
        assert start <= DAY <= end
        return [self.quote]


def test_index_biznesradar_priority_and_replay():
    db = DB()
    feeds = {"YFINANCE": Feed("YFINANCE", "3195"), "BIZNESRADAR": Feed("BIZNESRADAR", "3201")}
    first = run_market_job(
        db, kind="INDEX", feeds=feeds, start=DAY, end=DAY, write=True, symbols=["WIG20"]
    )
    assert (first.candidates_added, first.promoted) == (2, 1)
    result = db.conn.execute(
        "SELECT PROVIDER_ID,CANDIDATE_ID,SELECTION_REASON FROM INDEX_PRICES"
    ).fetchone()
    assert result[0] == 2 and isinstance(result[1], int)
    assert result[2] == "PRIORITY_SELECTED_DISAGREEMENT"
    same = run_market_job(
        db, kind="INDEX", feeds=feeds, start=DAY, end=DAY, write=True, symbols=["WIG20"]
    )
    assert same.candidates_added == 0 and same.identical == 1
    assert db.conn.execute("SELECT COUNT(*) FROM INDEX_PRICES").fetchone() == (1,)
    assert db.conn.execute("SELECT COUNT(*) FROM INDEX_PRICE_CANDIDATES").fetchone() == (2,)
    assert db.conn.execute(
        "SELECT COUNT(*) FROM INDEX_PRICES p JOIN INDEX_PRICE_CANDIDATES c "
        "ON p.CANDIDATE_ID=c.CANDIDATE_ID"
    ).fetchone() == (1,)


def test_derivative_futures_candidate_and_final_ohlcv():
    db = DB()
    out = run_market_job(
        db,
        kind="DERIVATIVE",
        feeds={"BIZNESRADAR": Feed("BIZNESRADAR", "3201", 12500)},
        start=DAY,
        end=DAY,
        write=True,
        symbols=["FW20Z2620"],
    )
    assert out.candidates_added == out.promoted == 1
    values = db.conn.execute(
        "SELECT VOLUME,PROVIDER_ID,CANDIDATE_ID FROM DERIVATIVE_PRICES"
    ).fetchone()
    assert values[0:2] == (12500, 2) and isinstance(values[2], int)
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM DERIVATIVE_PRICES p "
            "JOIN DERIVATIVE_PRICE_CANDIDATES c ON p.CANDIDATE_ID=c.CANDIDATE_ID"
        ).fetchone()[0]
        == 1
    )


def test_rejected_candidate_stored_but_not_promoted():
    db = DB()
    feeds = {"BIZNESRADAR": Feed("BIZNESRADAR", "3201", -5)}
    out = run_market_job(
        db, kind="DERIVATIVE", feeds=feeds, start=DAY, end=DAY, write=True, symbols=["FW20Z2620"]
    )
    assert out.rejected == 1 and out.promoted == 0
    assert db.conn.execute("SELECT COUNT(*) FROM DERIVATIVE_PRICE_CANDIDATES").fetchone() == (1,)
    assert db.conn.execute("SELECT COUNT(*) FROM DERIVATIVE_PRICES").fetchone() == (0,)


def test_preserves_conflicting_legacy_valid_close():
    db = DB()
    db.conn.execute(
        "INSERT INTO INDEX_PRICES (INDEX_SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,SOURCE) "
        "VALUES ('WIG20','2026-11-17',3200,3220,3190,3199,'manual')"
    )
    result = run_market_job(
        db,
        kind="INDEX",
        feeds={"BIZNESRADAR": Feed("BIZNESRADAR", "3201")},
        start=DAY,
        end=DAY,
        write=True,
        symbols=["WIG20"],
    )
    assert result.conflicts == 1 and result.candidates_added == 1
    assert db.conn.execute("SELECT CLOSE,SOURCE,CANDIDATE_ID FROM INDEX_PRICES").fetchone() == (
        3199,
        "manual",
        None,
    )


def test_index_has_no_traded_volume_and_derivative_requires_it():
    good = MarketCandle(DAY, Decimal(3), Decimal(4), Decimal(2), Decimal(3))
    assert validate("INDEX", good) == ("VALID", None)
    assert validate("DERIVATIVE", good)[0] == "REJECTED"
    m = ProviderMapping("WIG20", 2, "BIZNESRADAR", 2, "WIG20", 30, DAY, None)
    quote = make_candidate("INDEX", m, good)
    assert len(quote.content_hash) == 64
    assert quote.validation_status == "VALID"
    assert choose("INDEX", [quote])[0] == quote


def test_index_html_parser_without_volume_header():
    html = """<html><h1>Notowania historyczne WIG20</h1><p>GPW indeksy</p>
       <table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th><th>Zamknięcie</th></tr>
       <tr><td>17.11.2026</td><td>3200,00</td><td>3220,00</td>
           <td>3190,00</td><td>3201,00</td></tr></table></html>"""
    candles = parse_index_history(html, "WIG20")
    assert len(candles) == 1 and candles[0].volume is None
    assert candles[0].close == Decimal("3201.00")
    with pytest.raises(ValueError):
        parse_index_history(html.replace("WIG20", "mWIG40"), "WIG20")


def test_fallback_source_does_not_need_consensus():
    db = DB()

    class Broken:
        def history(self, symbol, start, end):
            raise ValueError("Stooq API key missing")

    result = run_market_job(
        db,
        kind="INDEX",
        feeds={"YFINANCE": Broken(), "BIZNESRADAR": Feed("BIZNESRADAR", "3201")},
        start=DAY,
        end=DAY,
        write=True,
        symbols=["WIG20"],
    )
    assert len(result.source_errors) == 1
    assert result.promoted == 1
    assert db.conn.execute("SELECT PROVIDER_ID FROM INDEX_PRICES").fetchone() == (2,)


def test_registered_option_with_zero_trades_can_store_quote():
    db = DB()
    db.conn.execute(
        "INSERT INTO DERIVATIVE_PROVIDER_SYMBOLS VALUES "
        "(4,'OW20C2600',2,'OW20C2600','2000-01-01',NULL,1)"
    )
    result = run_market_job(
        db,
        kind="DERIVATIVE",
        feeds={"BIZNESRADAR": Feed("BIZNESRADAR", "3201", 0)},
        start=DAY,
        end=DAY,
        write=True,
        symbols=["OW20C2600"],
    )
    assert result.promoted == 1
    assert db.conn.execute("SELECT VOLUME FROM DERIVATIVE_PRICES").fetchone() == (0,)
