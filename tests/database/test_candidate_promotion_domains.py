"""Promotion must read committed INDEX/DERIVATIVE/PRICES_CANDIDATES rows."""

from datetime import date
from decimal import Decimal

from database.candidate_promotion import promote_market_prices
from database.market_quote_pipeline import load_mappings, make_candidate, save_candidate
from ingestion.market_quote_feeds import MarketCandle
import re
import sqlite3


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
        sql = re.sub(r'CAST\(c\."DATE" AS DATE\)', 'DATE(c."DATE")', sql)
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


DAY = date(2026, 11, 17)


def _stage_one(db, kind, symbol, close="3201", volume=None):
    mapping = load_mappings(db, kind, symbols=[symbol])[symbol][0]
    quote = MarketCandle(
        DAY, Decimal("3200"), Decimal("3220"), Decimal("3190"), Decimal(close), volume
    )
    item = make_candidate(kind, mapping, quote)
    db.conn.execute("BEGIN")
    identity, created = save_candidate(db, kind, item, "snowflake")
    db.conn.execute("COMMIT")
    return identity, created


def test_index_staging_cannot_publish_then_promotion_can():
    db = DB()
    candidate_id, created = _stage_one(db, "INDEX", "WIG20")
    assert created
    assert db.conn.execute("SELECT COUNT(*) FROM INDEX_PRICE_CANDIDATES").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM INDEX_PRICES").fetchone()[0] == 0
    result = promote_market_prices(
        db, kind="INDEX", backend="snowflake", start=DAY, end=DAY, symbols=["WIG20"]
    )
    assert result.promoted == 1 and result.conflicts == 0
    assert db.conn.execute("SELECT CANDIDATE_ID FROM INDEX_PRICES").fetchone()[0] == candidate_id
    assert (
        promote_market_prices(db, kind="INDEX", backend="snowflake", start=DAY, end=DAY).identical
        == 1
    )


def test_derivative_staging_can_promote_futures():
    db = DB()
    candidate_id, _ = _stage_one(db, "DERIVATIVE", "FW20Z2620", volume=500)
    assert db.conn.execute("SELECT COUNT(*) FROM DERIVATIVE_PRICES").fetchone()[0] == 0
    result = promote_market_prices(db, kind="DERIVATIVE", backend="snowflake", start=DAY, end=DAY)
    assert result.promoted == 1
    assert db.conn.execute("SELECT CANDIDATE_ID,VOLUME FROM DERIVATIVE_PRICES").fetchone() == (
        candidate_id,
        500,
    )


def test_revised_candidate_does_not_overwrite_valid_historical_index():
    db = DB()
    _stage_one(db, "INDEX", "WIG20", close="3201")
    assert (
        promote_market_prices(db, kind="INDEX", backend="snowflake", start=DAY, end=DAY).promoted
        == 1
    )
    _stage_one(db, "INDEX", "WIG20", close="3202")
    result = promote_market_prices(db, kind="INDEX", backend="snowflake", start=DAY, end=DAY)
    assert result.conflicts == 1
    assert db.conn.execute("SELECT CLOSE FROM INDEX_PRICES").fetchone()[0] == 3201
