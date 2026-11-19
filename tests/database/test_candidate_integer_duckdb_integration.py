"""DuckDB schema is also integer-based; fresh and repeated setup are safe."""

import pytest


def test_new_duckdb_schema_has_numeric_id_and_hash(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from database.schema import prepare_tables

    conn = duckdb.connect(str(tmp_path / "candidates.duckdb"))
    try:
        prepare_tables(conn, backend="duckdb")
        prepare_tables(conn, backend="duckdb")
        columns = {
            row[1].lower(): row[2].upper()
            for row in conn.execute("PRAGMA table_info('prices_candidates')").fetchall()
        }
        assert columns["candidate_id"] == "BIGINT"
        assert columns["content_hash"].startswith("VARCHAR")
        price_columns = {
            row[1].lower(): row[2].upper()
            for row in conn.execute("PRAGMA table_info('prices')").fetchall()
        }
        assert price_columns["candidate_id"] == "BIGINT"
    finally:
        conn.close()
