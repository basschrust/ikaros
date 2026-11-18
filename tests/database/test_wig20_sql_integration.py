"""WIG20 one-command SQL integration using an in-memory SQLite database.

Only the DB-API adapter translates Snowflake-specific SQL. The test executes
production ingestion, candidate selection, and final PRICES promotion.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from types import ModuleType

from run import ingest_gpw_prices as cli

SESSIONS = cli.last_gpw_sessions(date(2026, 11, 17), 5)
SYMBOLS = [f"W{i:02d}.WA" for i in range(20)]


class SQLCursor:
    def __init__(self, inner):
        self.inner = inner

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(r'TO_DATE\("DATE"\)', 'DATE("DATE")', sql)
        sql = re.sub(r"TO_DATE\(\?,\s*'YYYY-MM-DD'\)", "DATE(?)", sql)
        sql = re.sub(r"TO_TIMESTAMP_NTZ\(\?,\s*'YYYY-MM-DD'\)", "?", sql)
        sql = re.sub(r"TO_DECIMAL\(\?,\s*\d+,\s*\d+\)", "?", sql)
        # SQLite INTEGER PRIMARY KEY auto-allocates an ID if given NULL.
        # Only the test adapter translates Snowflake's sequence NEXTVAL.
        sql = sql.replace('"PRICES_CANDIDATES_ID_SEQ".NEXTVAL', "NULL")
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
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE SECURITIES (ASSET_ID INTEGER PRIMARY KEY, SYMBOL TEXT,
                VENUE TEXT, INSTRUMENT_CLASS TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE PROVIDERS (PROVIDER_ID INTEGER PRIMARY KEY,
                PROVIDER_CODE TEXT, PRIORITY INTEGER, IS_ACTIVE BOOLEAN);
            CREATE TABLE PROVIDER_SYMBOLS (PROVIDER_SYMBOL_ID INTEGER PRIMARY KEY,
                ASSET_ID INTEGER, PROVIDER_ID INTEGER, PROVIDER_SYMBOL TEXT,
                VALID_FROM TEXT, VALID_TO TEXT, IS_ACTIVE BOOLEAN);
            CREATE TABLE PRICES_CANDIDATES (CANDIDATE_ID INTEGER PRIMARY KEY,
                CONTENT_HASH TEXT NOT NULL UNIQUE,
                ASSET_ID INTEGER, PROVIDER_ID INTEGER, PROVIDER_SYMBOL_ID INTEGER,
                SYMBOL TEXT, DATE TEXT, OPEN DECIMAL, HIGH DECIMAL, LOW DECIMAL,
                CLOSE DECIMAL, VOLUME DECIMAL, VALIDATION_STATUS TEXT,
                VALIDATION_REASON TEXT);
            CREATE TABLE PRICES (SYMBOL TEXT, DATE TEXT, ASSET_ID INTEGER,
                OPEN DECIMAL, HIGH DECIMAL, LOW DECIMAL, CLOSE DECIMAL,
                VOLUME DECIMAL, PROVIDER_ID INTEGER, CANDIDATE_ID INTEGER,
                SELECTED_AT TEXT, SELECTION_REASON TEXT,
                FOREIGN KEY (CANDIDATE_ID) REFERENCES PRICES_CANDIDATES(CANDIDATE_ID));
            """
        )
        self.conn.execute("INSERT INTO PROVIDERS VALUES (3,'BIZNESRADAR',30,1)")
        for number, symbol in enumerate(SYMBOLS, 1):
            self.conn.execute(
                "INSERT INTO SECURITIES VALUES (?,?,'GPW','stock',1)",
                (number, symbol),
            )
            self.conn.execute(
                "INSERT INTO PROVIDER_SYMBOLS VALUES (?,?,3,?,'2026-01-01',NULL,1)",
                (number, number, symbol[:-3]),
            )

    def cursor(self):
        return SQLCursor(self.conn.cursor())


class Source:
    def history(self, symbol, start, end):
        from ingestion.gpw_provider_feeds import RawCandle

        return [
            RawCandle(
                day,
                Decimal("75.60"),
                Decimal("76.24"),
                Decimal("75.16"),
                Decimal("75.96"),
                1_000_000,
            )
            for day in SESSIONS
            if start <= day <= end
        ]


def test_one_command_ingests_twenty_stocks_five_sessions_and_replays(monkeypatch):
    import ingestion.gpw_multi_source_job as job
    import ingestion.gpw_provider_feeds as feeds

    db = DB()
    cfg = ModuleType("config.config")
    cfg.load_config = lambda _file: {"database": {"driver": "snowflake"}}
    factory = ModuleType("database.repositories.factory")

    class Repo:
        connection = db

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    factory.repository_from_config = lambda _cfg: Repo()
    monkeypatch.setitem(sys.modules, "config.config", cfg)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory)
    monkeypatch.setattr(cli, "_warsaw_yesterday", lambda: date(2026, 11, 17))
    monkeypatch.setattr(cli, "wig20_symbols", lambda: SYMBOLS)
    monkeypatch.setattr(feeds, "default_feeds", lambda: {"BIZNESRADAR": Source()})
    monkeypatch.setattr(job.time, "sleep", lambda *_args: None)

    assert cli.main(["--wig20", "--write"]) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 100
    assert db.conn.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 100
    assert db.conn.execute("SELECT COUNT(DISTINCT ASSET_ID) FROM PRICES").fetchone()[0] == 20
    assert db.conn.execute("SELECT COUNT(DISTINCT DATE) FROM PRICES").fetchone()[0] == 5
    assert (
        db.conn.execute("SELECT COUNT(DISTINCT CONTENT_HASH) FROM PRICES_CANDIDATES").fetchone()[0]
        == 100
    )
    assert (
        db.conn.execute("SELECT COUNT(DISTINCT CANDIDATE_ID) FROM PRICES_CANDIDATES").fetchone()[0]
        == 100
    )
    joined = (
        "SELECT COUNT(*) FROM PRICES p JOIN PRICES_CANDIDATES c ON p.CANDIDATE_ID = c.CANDIDATE_ID"
    )
    assert db.conn.execute(joined).fetchone()[0] == 100

    # A second run reuses stored candidate IDs without changing either table.
    assert cli.main(["--wig20", "--write"]) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 100
    assert db.conn.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 100
    assert db.conn.execute(joined).fetchone()[0] == 100
    db.conn.close()
