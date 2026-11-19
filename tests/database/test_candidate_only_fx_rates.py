"""SQL-backed phase separation regression tests (SQLite adapter)."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal

from database.candidate_observations import Observation, stage, promote_stored


class Cursor:
    def __init__(self, database: sqlite3.Connection):
        self.inner = database.cursor()

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(r"CAST\(\? AS DATE\)", "date(?)", sql)
        sql = re.sub(r"CAST\(\"DATE\" AS DATE\)", 'date("DATE")', sql)
        sql = re.sub(r"CAST\(\? AS TIMESTAMP\)", "?", sql)
        sql = re.sub(
            r'"(FX_RATE_CANDIDATES|RATE_OBSERVATION_CANDIDATES)_ID_SEQ"\.NEXTVAL',
            lambda m: f'(SELECT COALESCE(MAX(CANDIDATE_ID),0)+1 FROM "{m.group(1)}")',
            sql,
        )
        self.inner.execute(sql, params)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    def close(self):
        self.inner.close()


class DB:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript("""
            CREATE TABLE FX_RATE_CANDIDATES (
                CANDIDATE_ID INTEGER PRIMARY KEY, CONTENT_HASH TEXT UNIQUE,
                CURRENCY_PAIR_ID INTEGER, SYMBOL TEXT, DATE DATE,
                SOURCE_CODE TEXT, QUOTE_KIND TEXT, OPEN TEXT, HIGH TEXT, LOW TEXT,
                CLOSE TEXT, VOLUME INTEGER, VALIDATION_STATUS TEXT, VALIDATION_REASON TEXT);
            CREATE TABLE RATE_OBSERVATION_CANDIDATES (
                CANDIDATE_ID INTEGER PRIMARY KEY, CONTENT_HASH TEXT UNIQUE,
                SERIES_ID TEXT, DATE DATE, SOURCE_CODE TEXT, RATE TEXT, BID TEXT, ASK TEXT,
                VALIDATION_STATUS TEXT, VALIDATION_REASON TEXT);
            CREATE TABLE FX_RATES (
                CURRENCY_PAIR_ID INTEGER, SYMBOL TEXT, DATE DATE,
                OPEN TEXT,HIGH TEXT,LOW TEXT,CLOSE TEXT,VOLUME INTEGER,
                CANDIDATE_ID INTEGER,SOURCE_CODE TEXT,SELECTION_REASON TEXT,SELECTED_AT TEXT);
            CREATE TABLE RATE_OBSERVATIONS (
                SERIES_ID TEXT, DATE DATE,RATE TEXT,SOURCE TEXT,BID TEXT,ASK TEXT,
                CANDIDATE_ID INTEGER,SELECTION_REASON TEXT,SELECTED_AT TEXT);
        """)

    def cursor(self):
        return Cursor(self.db)

    def count(self, table):
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fx(day=date(2026, 11, 18), source="YFINANCE", close="4.2800", kind="MARKET"):
    fields = (
        {"open": Decimal("4.27"), "high": Decimal("4.29"), "low": Decimal("4.26")}
        if kind == "MARKET"
        else {}
    )
    return Observation(
        "FX", "EURPLN=X", day, source, ref_id=42, quote_kind=kind, close=Decimal(close), **fields
    )


def test_fx_source_only_stages_then_promoter_reads_persisted_rows():
    db = DB()
    obs = fx()
    assert stage(db, [obs], backend="snowflake", write=True) == (1, 1)
    assert db.count("FX_RATE_CANDIDATES") == 1
    assert db.count("FX_RATES") == 0
    assert stage(db, [obs], backend="snowflake", write=True) == (1, 0)
    report = promote_stored(db, kind="FX", backend="snowflake", start=obs.day, end=obs.day)
    assert (report.considered, report.promoted, report.conflicts) == (1, 1, 0)
    pair = db.db.execute("SELECT CANDIDATE_ID, CLOSE, SOURCE_CODE FROM FX_RATES").fetchone()
    assert pair == (1, "4.28000000", "YFINANCE")
    assert (
        promote_stored(db, kind="FX", backend="snowflake", start=obs.day, end=obs.day).identical
        == 1
    )
    assert db.count("FX_RATES") == 1


def test_two_sources_do_not_mix_or_overwrite_conflicting_existing_fx():
    db = DB()
    day = date(2026, 11, 18)
    stage(
        db, [fx(day, kind="REFERENCE", source="NBP", close="4.12")], backend="snowflake", write=True
    )
    r = promote_stored(db, kind="FX", backend="snowflake", start=day, end=day)
    assert r.promoted == 1
    assert db.db.execute("SELECT OPEN FROM FX_RATES").fetchone()[0] is None
    stage(db, [fx(day, source="YFINANCE", close="4.28")], backend="snowflake", write=True)
    r = promote_stored(db, kind="FX", backend="snowflake", start=day, end=day)
    assert r.conflicts == 1
    assert db.db.execute("SELECT CLOSE FROM FX_RATES").fetchone()[0] == "4.12000000"
    assert db.count("FX_RATE_CANDIDATES") == 2


def test_rate_negatives_are_allowed_and_promoted_only_after_stage():
    db = DB()
    date_ = date(2026, 11, 18)
    obs = Observation(
        "RATE",
        "ECB_NEG_EUR_1Y",
        date_,
        "ECB",
        quote_kind="RATE",
        rate=Decimal("-0.0041"),
        bid=Decimal("-0.0042"),
        ask=Decimal("-0.0040"),
    )
    assert stage(db, [obs], backend="snowflake", write=True) == (1, 1)
    assert db.count("RATE_OBSERVATIONS") == 0
    report = promote_stored(db, kind="RATE", backend="snowflake", start=date_, end=date_)
    assert report.promoted == 1
    assert db.db.execute("SELECT RATE FROM RATE_OBSERVATIONS").fetchone()[0] == "-0.0041000000"
    assert (
        promote_stored(db, kind="RATE", backend="snowflake", start=date_, end=date_).identical == 1
    )


def test_rejected_rate_is_stored_but_never_promoted():
    db = DB()
    day = date(2026, 11, 18)
    bad = Observation("RATE", "UST_1Y", day, "UST", quote_kind="RATE", rate=Decimal("NaN"))
    assert stage(db, [bad], backend="snowflake", write=True) == (1, 1)
    assert (
        db.db.execute("SELECT VALIDATION_STATUS FROM RATE_OBSERVATION_CANDIDATES").fetchone()[0]
        == "REJECTED"
    )
    result = promote_stored(db, kind="RATE", backend="snowflake", start=day, end=day)
    assert result.invalid == 1 and result.promoted == 0
    assert db.count("RATE_OBSERVATIONS") == 0


def test_single_source_corrected_observation_is_a_new_candidate_but_not_silent_reprice():
    db = DB()
    day = date(2026, 11, 18)
    stage(db, [fx(day)], backend="snowflake", write=True)
    promote_stored(db, kind="FX", backend="snowflake", start=day, end=day)
    stage(
        db,
        [
            Observation(
                "FX",
                "EURPLN=X",
                day,
                "YFINANCE",
                ref_id=42,
                open=Decimal("4.29"),
                high=Decimal("4.31"),
                low=Decimal("4.28"),
                close=Decimal("4.30"),
            )
        ],
        backend="snowflake",
        write=True,
    )
    result = promote_stored(db, kind="FX", backend="snowflake", start=day, end=day)
    assert result.conflicts == 1
    assert db.count("FX_RATE_CANDIDATES") == 2
    assert db.db.execute("SELECT CLOSE FROM FX_RATES").fetchone()[0] == "4.28000000"


def test_rate_existing_other_source_is_not_relinked_to_new_candidate():
    db = DB()
    day = date(2026, 11, 18)
    db.db.execute(
        "INSERT INTO RATE_OBSERVATIONS (SERIES_ID, DATE, RATE, SOURCE) VALUES (?,?,?,?)",
        ("TEST_RATE", day.isoformat(), "0.025", "OTHER_PROVIDER"),
    )
    stage(
        db,
        [Observation("RATE", "TEST_RATE", day, "UST", quote_kind="RATE", rate=Decimal("0.025"))],
        backend="snowflake",
        write=True,
    )
    result = promote_stored(db, kind="RATE", backend="snowflake", start=day, end=day)
    assert result.conflicts == 1
    assert db.db.execute("SELECT CANDIDATE_ID, SOURCE FROM RATE_OBSERVATIONS").fetchone() == (
        None,
        "OTHER_PROVIDER",
    )
