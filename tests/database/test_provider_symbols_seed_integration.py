"""SQL-backed integration tests for Snowflake seeding via DB-API adapter.

SQLite stores real rows and enforces PK/FK; the adapter only translates Snowflake
%s placeholders to SQLite ? placeholders. No network, no live database writes.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from database.provider_symbols_seed import seed_provider_symbols


class Cursor:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def execute(self, sql: str, params: tuple = ()) -> "Cursor":
        self._cursor.execute(
            sql.replace("%s", "?"),
            tuple(item.isoformat() if isinstance(item, date) else item for item in params),
        )
        return self

    def fetchall(self) -> list[tuple]:
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """CREATE TABLE "SECURITIES" (
                "ASSET_ID" BIGINT PRIMARY KEY, "SYMBOL" VARCHAR,
                "VENUE" VARCHAR, "INSTRUMENT_CLASS" VARCHAR, "IS_ACTIVE" BOOLEAN
            );
            CREATE TABLE "PROVIDERS" (
                "PROVIDER_ID" BIGINT PRIMARY KEY, "PROVIDER_CODE" VARCHAR UNIQUE,
                "NAME" VARCHAR, "PRIORITY" INTEGER, "IS_ACTIVE" BOOLEAN
            );
            CREATE TABLE "PROVIDER_SYMBOLS" (
                "PROVIDER_SYMBOL_ID" BIGINT PRIMARY KEY,
                "ASSET_ID" BIGINT REFERENCES "SECURITIES"("ASSET_ID"),
                "PROVIDER_ID" BIGINT REFERENCES "PROVIDERS"("PROVIDER_ID"),
                "PROVIDER_SYMBOL" VARCHAR, "VALID_FROM" DATE, "VALID_TO" DATE
            );"""
        )
        self.db.executemany(
            'INSERT INTO "SECURITIES" VALUES (?,?,?,?,?)',
            [
                (101, "PZU.WA", "GPW", "stock", 1),
                (102, "PKO.WA", "GPW", "stock", 1),
                (103, "FW20", "GPW", "future", 1),
                (104, "INTC", "US", "stock", 1),
            ],
        )

    def cursor(self) -> Cursor:
        return Cursor(self.db.cursor())

    def count(self, table: str) -> int:
        return self.db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def close(self) -> None:
        self.db.close()


def test_dry_run_has_no_effects():
    connection = Connection()
    try:
        plan = seed_provider_symbols(connection)
        assert len(plan.providers) == 3
        assert len(plan.mappings) == 2
        assert connection.count("PROVIDERS") == 0
        assert connection.count("PROVIDER_SYMBOLS") == 0
    finally:
        connection.close()


def test_write_and_rerun_is_idempotent_with_reviewed_pzu_mapping(tmp_path: Path):
    path = tmp_path / "reviewed.csv"
    path.write_text(
        "canonical_symbol,venue,provider_code,provider_symbol,valid_from,valid_to\n"
        "PZU.WA,GPW,STOOQ,pzu,1900-01-01,\n"
        "PZU.WA,GPW,BIZNESRADAR,PZU,1900-01-01,\n",
        encoding="utf-8",
    )
    connection = Connection()
    try:
        first = seed_provider_symbols(connection, csv_path=path, write=True)
        assert len(first.providers) == 3
        assert len(first.mappings) == 4
        assert connection.count("PROVIDERS") == 3
        assert connection.count("PROVIDER_SYMBOLS") == 4
        rows = connection.db.execute(
            'SELECT ps."ASSET_ID", p."PROVIDER_CODE", ps."PROVIDER_SYMBOL" '
            'FROM "PROVIDER_SYMBOLS" ps JOIN "PROVIDERS" p '
            'ON ps."PROVIDER_ID"=p."PROVIDER_ID" ORDER BY p."PROVIDER_CODE",ps."ASSET_ID"'
        ).fetchall()
        assert (101, "YFINANCE", "PZU.WA") in rows
        assert (101, "STOOQ", "pzu") in rows
        assert (101, "BIZNESRADAR", "PZU") in rows
        assert (102, "YFINANCE", "PKO.WA") in rows
        assert connection.db.execute(
            'SELECT "IS_ACTIVE" FROM "PROVIDERS" WHERE "PROVIDER_CODE"="STOOQ"'
        ).fetchone() == (0,)
        second = seed_provider_symbols(connection, csv_path=path, write=True)
        assert not second.providers and not second.mappings
        assert second.existing_count == 4
        assert connection.count("PROVIDERS") == 3
        assert connection.count("PROVIDER_SYMBOLS") == 4
        assert connection.db.execute(
            'SELECT "PROVIDER_SYMBOL_ID" FROM "PROVIDER_SYMBOLS"'
        ).fetchall()
    finally:
        connection.close()


def test_existing_provider_ids_are_preserved():
    connection = Connection()
    try:
        connection.db.execute('INSERT INTO "PROVIDERS" VALUES (99,"YFINANCE","My Yahoo",1,1)')
        plan = seed_provider_symbols(connection, write=True)
        assert len(plan.providers) == 2
        got = connection.db.execute(
            'SELECT "PROVIDER_ID","NAME" FROM "PROVIDERS" WHERE "PROVIDER_CODE"="YFINANCE"'
        ).fetchone()
        assert got == (99, "My Yahoo")
        ids = connection.db.execute(
            'SELECT DISTINCT "PROVIDER_ID" FROM "PROVIDER_SYMBOLS"'
        ).fetchall()
        assert ids == [(99,)]
    finally:
        connection.close()


def test_conflicting_existing_provider_mapping_blocks_all_writes(tmp_path: Path):
    connection = Connection()
    path = tmp_path / "reviewed.csv"
    path.write_text(
        "canonical_symbol,venue,provider_code,provider_symbol,valid_from,valid_to\n"
        "PZU.WA,GPW,STOOQ,pzu,1900-01-01,\n",
        encoding="utf-8",
    )
    try:
        connection.db.execute('INSERT INTO "PROVIDERS" VALUES (88,"STOOQ","Stooq",20,0)')
        connection.db.execute(
            'INSERT INTO "PROVIDER_SYMBOLS" VALUES (2,102,88,"pzu","1900-01-01",NULL)'
        )
        with pytest.raises(ValueError, match="already maps"):
            seed_provider_symbols(connection, csv_path=path, write=True)
        # Even the missing Yahoo provider must not be inserted after conflict.
        assert connection.count("PROVIDERS") == 1
        assert connection.count("PROVIDER_SYMBOLS") == 1
    finally:
        connection.close()


def test_missing_securities_fails_without_provider_changes():
    connection = Connection()
    try:
        connection.db.execute('DELETE FROM "SECURITIES"')
        with pytest.raises(ValueError, match="seed SECURITIES first"):
            seed_provider_symbols(connection, write=True)
        assert connection.count("PROVIDERS") == 0
    finally:
        connection.close()


def test_activate_alternatives_explicit_and_idempotent(tmp_path: Path):
    reviewed = tmp_path / "mappings.csv"
    reviewed.write_text(
        "canonical_symbol,venue,provider_code,provider_symbol,valid_from,valid_to\n"
        "PZU.WA,GPW,STOOQ,pzu,1900-01-01,\n"
        "PZU.WA,GPW,BIZNESRADAR,PZU,1900-01-01,\n",
        encoding="utf-8",
    )
    conn = Connection()
    try:
        first = seed_provider_symbols(
            conn, csv_path=reviewed, write=True, activate_alternatives=True
        )
        assert len(first.activate_ids) == 2
        values = conn.db.execute(
            'SELECT "IS_ACTIVE" FROM "PROVIDERS" WHERE "PROVIDER_CODE" IN ("STOOQ","BIZNESRADAR")'
        ).fetchall()
        assert values == [(1,), (1,)]
        repeat = seed_provider_symbols(
            conn, csv_path=reviewed, write=True, activate_alternatives=True
        )
        assert not repeat.providers and not repeat.mappings
        assert conn.count("PROVIDERS") == 3
    finally:
        conn.close()


def test_skip_unknown_reviewed_symbol_only_if_requested(tmp_path: Path):
    reviewed = tmp_path / "mappings.csv"
    reviewed.write_text(
        "canonical_symbol,venue,provider_code,provider_symbol,valid_from,valid_to\n"
        "PZU.WA,GPW,STOOQ,pzu,1900-01-01,\n"
        "MISSING.WA,GPW,STOOQ,missing,1900-01-01,\n",
        encoding="utf-8",
    )
    conn = Connection()
    try:
        with pytest.raises(ValueError, match="unknown/inactive security"):
            seed_provider_symbols(conn, csv_path=reviewed)
        plan = seed_provider_symbols(conn, csv_path=reviewed, skip_unregistered=True)
        assert len(plan.mappings) == 3  # Yahoo for PZU/PKO and reviewed Stooq PZU
    finally:
        conn.close()
