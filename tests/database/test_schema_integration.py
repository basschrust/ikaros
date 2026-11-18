"""Local DuckDB integration tests. Requires project dependency duckdb."""

from __future__ import annotations
from decimal import Decimal
import pytest
from database.schema import prepare_tables, diagnostics


duckdb = pytest.importorskip("duckdb")


def test_fresh_install_and_rerun(tmp_path):
    conn = duckdb.connect(str(tmp_path / "fresh.duckdb"))
    prepare_tables(conn, backend="duckdb")
    prepare_tables(conn, backend="duckdb")
    tables = {r[0].lower() for r in conn.execute("SHOW TABLES").fetchall()}
    assert {"securities", "prices", "providers", "provider_symbols", "prices_candidates"} <= tables
    conn.execute("INSERT INTO securities (asset_id,symbol) VALUES (1001,'PZU.WA')")
    conn.execute(
        "INSERT INTO providers (provider_id,provider_code,name,priority) VALUES (1,'YFINANCE','Yahoo Finance',10)"
    )
    conn.execute(
        "INSERT INTO provider_symbols (provider_symbol_id,asset_id,provider_id,provider_symbol,valid_from) VALUES (1,1001,1,'PZU.WA','2026-01-01')"
    )
    conn.execute(
        "INSERT INTO prices_candidates (candidate_id,asset_id,provider_id,provider_symbol_id,symbol,date,open,high,low,close,volume) VALUES ('test-c1',1001,1,1,'PZU.WA','2026-11-16',60.01,62.01,59.01,61.01,123456)"
    )
    row = conn.execute("SELECT asset_id,close,volume FROM prices_candidates").fetchone()
    assert row == (1001, Decimal("61.01000000"), Decimal("123456"))
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO prices_candidates (candidate_id,asset_id,provider_id,provider_symbol_id,symbol,date,close) VALUES ('bad',99999,1,1,'PZU.WA','2026-11-16',61)"
        )
    conn.close()


def test_existing_legacy_prices_backfilled_safely(tmp_path):
    conn = duckdb.connect(str(tmp_path / "legacy.duckdb"))
    conn.execute("CREATE TABLE securities (asset_id BIGINT, symbol VARCHAR)")
    conn.execute("CREATE TABLE prices (symbol VARCHAR, date TIMESTAMP, close DOUBLE)")
    conn.execute("INSERT INTO securities VALUES (7,'PZU.WA')")
    conn.execute(
        "INSERT INTO prices VALUES ('PZU.WA','2026-11-16',61.25),('UNKNOWN.WA','2026-11-16',10)"
    )
    prepare_tables(conn, backend="duckdb")
    assert conn.execute("SELECT asset_id FROM prices WHERE symbol='PZU.WA'").fetchone()[0] == 7
    assert (
        conn.execute("SELECT asset_id FROM prices WHERE symbol='UNKNOWN.WA'").fetchone()[0] is None
    )
    assert diagnostics(conn)["unmapped_prices"] == 1
    prepare_tables(conn, backend="duckdb")
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    conn.close()


def test_duplicate_asset_ids_refused(tmp_path):
    conn = duckdb.connect(str(tmp_path / "duplicate.duckdb"))
    conn.execute("CREATE TABLE securities (asset_id BIGINT, symbol VARCHAR)")
    conn.execute("INSERT INTO securities VALUES (1,'X'),(1,'Y')")
    with pytest.raises(ValueError, match="non-null and unique"):
        prepare_tables(conn, backend="duckdb")
    conn.close()
