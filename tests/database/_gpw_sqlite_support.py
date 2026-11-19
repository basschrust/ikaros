"""SQL-backed test adapter for Snowflake GPW insert/selection statements."""

from __future__ import annotations

import re
import sqlite3


class Cursor:
    def __init__(self, inner: sqlite3.Cursor):
        self.inner = inner

    def execute(self, sql: str, params=()):
        sql = sql.replace("%s", "?")
        sql = re.sub(r'TO_DATE\("DATE"\)', 'DATE("DATE")', sql)
        sql = re.sub(r"TO_DATE\(\?,\s*'YYYY-MM-DD'\)", "DATE(?)", sql)
        sql = re.sub(r"TO_TIMESTAMP_NTZ\(\?,\s*'YYYY-MM-DD'\)", "?", sql)
        sql = re.sub(r"TO_DECIMAL\(\?,\s*\d+,\s*\d+\)", "?", sql)
        sql = sql.replace('"PRICES_CANDIDATES_ID_SEQ".NEXTVAL', "NULL")
        sql = sql.replace("CURRENT_TIMESTAMP()", "CURRENT_TIMESTAMP")
        self.inner.execute(sql, params)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    @property
    def rowcount(self):
        return self.inner.rowcount

    def close(self) -> None:
        self.inner.close()


class Database:
    def __init__(self, *, existing_volume=None):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
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

    def close(self):
        self.db.close()
