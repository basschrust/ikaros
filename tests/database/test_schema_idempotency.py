"""Offline Snowflake catalog tests: schema and candidate-ID sequence are rerunnable."""

from __future__ import annotations

import pytest

from database.schema import BASE_COLUMNS, NEW_COLUMNS, _type, prepare_tables


class SnowflakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql):
        self.connection.statements.append(sql)
        upper = sql.upper()
        if upper.startswith("CREATE TABLE IF NOT EXISTS"):
            table = upper.split('"')[1]
            self.connection.tables.setdefault(
                table, set(c.upper() for c in ((BASE_COLUMNS | NEW_COLUMNS)[table.lower()]))
            )
        elif upper.startswith("CREATE SEQUENCE IF NOT EXISTS"):
            self.connection.sequence_exists = True
        elif upper.startswith('SELECT "COLUMN_NAME" FROM INFORMATION_SCHEMA.COLUMNS'):
            table = upper.split("TABLE_NAME = '")[1].split("'")[0]
            self.rows = [(c,) for c in self.connection.tables[table]]
        elif upper.startswith("ALTER TABLE") and " ADD COLUMN " in upper:
            table, column = upper.split('"')[1], upper.split('"')[3]
            if column in self.connection.tables[table]:
                raise AssertionError(f"repeated ALTER for {table}.{column}")
            self.connection.tables[table].add(column)
        elif 'SELECT "ASSET_ID", COUNT(*)' in upper:
            self.rows = []
        elif "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS" in upper:
            self.rows = [(int(self.connection.primary_key),)]
        elif " ADD CONSTRAINT " in upper:
            self.connection.primary_key = True
        elif upper.startswith('UPDATE "PRICES"'):
            pass
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")
        return self

    def fetchall(self):
        return self.rows


class SnowflakeConnection:
    def __init__(self):
        self.statements = []
        self.tables = {}
        self.primary_key = False
        self.sequence_exists = False

    def cursor(self):
        return SnowflakeCursor(self)


def test_fresh_then_repeat_has_no_repeated_alter():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    first_count = len(conn.statements)
    prepare_tables(conn, backend="snowflake")
    second = conn.statements[first_count:]
    assert len(conn.tables) == len(BASE_COLUMNS) + len(NEW_COLUMNS) == 11
    assert conn.sequence_exists
    assert not any(" ADD COLUMN " in s or " ADD CONSTRAINT " in s for s in second)
    assert all("OR REPLACE SEQUENCE" not in s for s in conn.statements)


def test_existing_adds_only_missing_columns():
    conn = SnowflakeConnection()
    conn.tables["SECURITIES"] = {"ASSET_ID", "SYMBOL"}
    conn.tables["PRICES"] = {"SYMBOL", "DATE", "CLOSE", "ASSET_ID", "PROVIDER_ID"}
    prepare_tables(conn, backend="snowflake")
    added = [s for s in conn.statements if " ADD COLUMN " in s]
    assert len(added) == 4
    assert any('"IS_ACTIVE"' in s for s in added)
    assert not any('"PROVIDER_ID"' in s for s in added)
    before = len(conn.statements)
    prepare_tables(conn, backend="snowflake")
    assert not any(" ADD COLUMN " in s for s in conn.statements[before:])


def test_timestamp_default_kept():
    assert _type("TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP", "snowflake") == (
        "TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
    )
    assert _type("DECIMAL(24,8)", "snowflake") == "NUMBER(24,8)"
    assert _type("BIGINT", "snowflake") == "NUMBER(38,0)"


def test_unknown_backend():
    with pytest.raises(ValueError, match="Unknown backend"):
        prepare_tables(SnowflakeConnection(), backend="sqlite")
