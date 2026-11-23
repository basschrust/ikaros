"""Regression for Snowflake timestamp syntax during local DuckDB promotion."""

import pytest

from database.duckdb_compat import CompatibleConnection, duckdb_sql


def test_sql_translation_removes_snowflake_timestamp_function_call():
    query = (
        'INSERT INTO "PRICES" ("DATE","SELECTED_AT") '
        "VALUES (TO_TIMESTAMP_NTZ(%s,'YYYY-MM-DD'),CURRENT_TIMESTAMP())"
    )
    assert duckdb_sql(query) == (
        'INSERT INTO "PRICES" ("DATE","SELECTED_AT") '
        "VALUES (CAST(? AS TIMESTAMP),CURRENT_TIMESTAMP)"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT current_timestamp()",
        "SELECT CURRENT_TIMESTAMP ( )",
        "SELECT Current_TimeStamp()",
    ],
)
def test_translation_case_and_whitespace(sql):
    assert duckdb_sql(sql) == "SELECT CURRENT_TIMESTAMP"


def test_translation_preserves_standard_timestamp_keyword():
    assert duckdb_sql("SELECT CURRENT_TIMESTAMP") == "SELECT CURRENT_TIMESTAMP"


def test_translation_handles_equity_upsert_statement():
    sql = (
        'INSERT INTO "PRICES" ("DATE","CLOSE","VOLUME","SELECTED_AT") '
        "VALUES (TO_TIMESTAMP_NTZ(%s,'YYYY-MM-DD'),"
        "TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,38,0),CURRENT_TIMESTAMP())"
    )
    result = duckdb_sql(sql)
    assert "CURRENT_TIMESTAMP()" not in result
    assert "CURRENT_TIMESTAMP" in result
    assert "CAST(? AS DECIMAL(24,8))" in result
    assert "CAST(? AS DECIMAL(38,0))" in result
    assert "CAST(? AS TIMESTAMP)" in result


def test_sql_executes_in_real_duckdb_when_available():
    duckdb = pytest.importorskip("duckdb")
    raw = duckdb.connect(":memory:")
    conn = CompatibleConnection(raw)
    try:
        conn.execute('CREATE TABLE "PRICES" ("DATE" TIMESTAMP, "SELECTED_AT" TIMESTAMP)')
        conn.execute(
            'INSERT INTO "PRICES" ("DATE","SELECTED_AT") '
            "VALUES (TO_TIMESTAMP_NTZ(%s,'YYYY-MM-DD'),CURRENT_TIMESTAMP())",
            ("2025-01-02",),
        )
        result = conn.execute(
            'SELECT CAST("DATE" AS DATE), "SELECTED_AT" IS NOT NULL FROM "PRICES"'
        ).fetchone()
        assert str(result[0]) == "2025-01-02"
        assert result[1] is True
    finally:
        conn.close()
