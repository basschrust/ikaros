"""Real DuckDB integration: fresh instance, repeated initialization, legacy upgrade."""

import pytest
from database.schema import prepare_tables


def test_duckdb_fresh_and_repeat():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    try:
        prepare_tables(con, backend="duckdb")
        prepare_tables(con, backend="duckdb")
        tables = {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
        assert {
            "securities",
            "prices",
            "providers",
            "provider_symbols",
            "prices_candidates",
        } <= tables
    finally:
        con.close()


def test_duckdb_existing_rows_preserved():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE securities (asset_id BIGINT, symbol VARCHAR)")
        con.execute("INSERT INTO securities VALUES (101, 'PZU.WA')")
        con.execute("CREATE TABLE prices (symbol VARCHAR, date TIMESTAMP, close DOUBLE)")
        con.execute("INSERT INTO prices VALUES ('PZU.WA', '2026-10-20', 51.23)")
        prepare_tables(con, backend="duckdb")
        prepare_tables(con, backend="duckdb")
        result = con.execute("SELECT symbol, close, asset_id FROM prices").fetchone()
        assert result == ("PZU.WA", 51.23, 101)
    finally:
        con.close()
