"""Regression coverage for timestamp-default SQL on both backends."""

from __future__ import annotations

import pytest

from database.schema import BASE_COLUMNS, NEW_COLUMNS, _type, prepare_tables


@pytest.mark.parametrize(
    "sql",
    [
        "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "TIMESTAMP",
    ],
)
def test_snowflake_timestamp_type_preserves_default(sql: str) -> None:
    translated = _type(sql, "snowflake")
    assert translated.startswith("TIMESTAMP_NTZ")
    assert "CURRENT_TIMESTAMP_NTZ" not in translated
    if "DEFAULT" in sql:
        assert "DEFAULT CURRENT_TIMESTAMP" in translated


def test_all_snowflake_definitions_are_free_of_invalid_timestamp_function() -> None:
    for columns in (*BASE_COLUMNS.values(), *NEW_COLUMNS.values()):
        for column_type in columns.values():
            assert "CURRENT_TIMESTAMP_NTZ" not in _type(column_type, "snowflake")


def test_duckdb_definitions_unchanged() -> None:
    for columns in (*BASE_COLUMNS.values(), *NEW_COLUMNS.values()):
        for column_type in columns.values():
            assert _type(column_type, "duckdb") == column_type


class RecordingCursor:
    def __init__(self, recorder: "RecordingConnection") -> None:
        self.recorder = recorder

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def execute(self, sql: str) -> None:
        self.recorder.statements.append(sql)

    def fetchall(self) -> list[tuple[int, ...]]:
        return (
            [(0,)]
            if "COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS" in self.recorder.statements[-1]
            else []
        )


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)


def test_snowflake_prepare_tables_generates_valid_default_expressions() -> None:
    connection = RecordingConnection()
    prepare_tables(connection, backend="snowflake")
    ddl = "\n".join(connection.statements)
    assert 'CREATE TABLE IF NOT EXISTS "PROVIDERS"' in ddl
    assert 'CREATE TABLE IF NOT EXISTS "PROVIDER_SYMBOLS"' in ddl
    assert 'CREATE TABLE IF NOT EXISTS "PRICES_CANDIDATES"' in ddl
    assert "CURRENT_TIMESTAMP_NTZ" not in ddl
    assert "TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in ddl
    assert 'UPDATE "PRICES"' in ddl
