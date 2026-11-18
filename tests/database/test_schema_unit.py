"""Offline schema declaration tests (candidate IDs are integer on both backends)."""

from __future__ import annotations

import pytest
from database.schema import BASE_COLUMNS, NEW_COLUMNS, _type, prepare_tables


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


def test_snowflake_creates_integer_candidate_id_and_hash():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    ddl = "\n".join(conn.statements)
    for name in (*BASE_COLUMNS, *NEW_COLUMNS):
        assert f'CREATE TABLE IF NOT EXISTS "{name.upper()}"' in ddl
    assert '"CANDIDATE_ID" NUMBER(38,0) PRIMARY KEY' in ddl
    assert '"CONTENT_HASH" VARCHAR(64) NOT NULL UNIQUE' in ddl
    assert '"CANDIDATE_ID" NUMBER(38,0)' in ddl  # PRICES reference
    assert 'CREATE SEQUENCE IF NOT EXISTS "PRICES_CANDIDATES_ID_SEQ"' in ddl
    assert '"CLOSE" NUMBER(24,8) NOT NULL' in ddl
    assert '"VOLUME" NUMBER(38,0)' in ddl
    assert 'FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID")' in ddl
    assert 'UPDATE "PRICES"' in ddl
    assert "DROP TABLE" not in ddl.upper()
    assert "CHECK (" not in ddl


def test_snowflake_schema_repeat_safe():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    prepare_tables(conn, backend="snowflake")
    assert not any("DROP TABLE" in sql for sql in conn.statements)
    assert all("OR REPLACE SEQUENCE" not in sql for sql in conn.statements)


def test_bad_backend_is_rejected():
    with pytest.raises(ValueError, match="backend"):
        prepare_tables(SnowflakeConnection(), backend="mysql")


def test_canonical_primary_keys_are_integer():
    assert BASE_COLUMNS["prices"]["candidate_id"] == "BIGINT"
    assert NEW_COLUMNS["prices_candidates"]["candidate_id"] == "BIGINT PRIMARY KEY"
    assert "content_hash" in NEW_COLUMNS["prices_candidates"]
    assert "provider_symbol_id" in NEW_COLUMNS["prices_candidates"]
    assert _type("BIGINT", "snowflake") == "NUMBER(38,0)"
