"""Offline Snowflake schema-contract tests; no credentials or network needed."""

from __future__ import annotations
from database.schema import BASE_COLUMNS, NEW_COLUMNS, prepare_tables
import pytest


class Cursor:
    def __init__(self, connection):
        self.conn = connection
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql):
        self.statement = sql
        self.conn.statements.append(sql)

    def fetchall(self):
        if "COUNT(*)" in self.statement and "GROUP BY" not in self.statement:
            return [(0,)]
        return []


class SnowflakeConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return Cursor(self)


def test_full_schema_has_eleven_tables():
    assert len(BASE_COLUMNS) == 8
    assert len(NEW_COLUMNS) == 3
    assert set(NEW_COLUMNS) == {"providers", "provider_symbols", "prices_candidates"}


def test_snowflake_create_all_and_extensions():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    ddl = "\n".join(conn.statements)
    for name in (*BASE_COLUMNS, *NEW_COLUMNS):
        assert f'CREATE TABLE IF NOT EXISTS "{name.upper()}"' in ddl
    assert 'ALTER TABLE "PRICES" ADD COLUMN IF NOT EXISTS "ASSET_ID" NUMBER(38,0)' in ddl
    assert '"CLOSE" NUMBER(24,8) NOT NULL' in ddl
    assert '"VOLUME" NUMBER(38,0)' in ddl
    assert 'FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID")' in ddl
    assert 'UPDATE "PRICES"' in ddl
    assert "DROP TABLE" not in ddl.upper()
    assert "CHECK (" not in ddl  # Snowflake standard tables do not enforce CHECK


def test_snowflake_ddl_can_be_reissued():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    first = list(conn.statements)
    prepare_tables(conn, backend="snowflake")
    assert conn.statements == first + first  # mocked metadata represents an uninitialized DB


def test_bad_backend_is_rejected():
    with pytest.raises(ValueError, match="backend"):
        prepare_tables(SnowflakeConnection(), backend="mysql")


def test_unambiguous_canonical_asset_id_remains_primary():
    assert "asset_id" in BASE_COLUMNS["prices"]
    assert "provider_id" in BASE_COLUMNS["prices"]
    assert "provider_symbol_id" in NEW_COLUMNS["prices_candidates"]
