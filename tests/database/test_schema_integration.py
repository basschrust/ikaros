"""Local DuckDB schema integration tests for integer candidate primary keys."""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.schema import diagnostics, prepare_tables


duckdb = pytest.importorskip("duckdb")


def test_fresh_install_and_rerun(tmp_path):
    conn = duckdb.connect(str(tmp_path / "fresh.duckdb"))
    try:
        prepare_tables(conn, backend="duckdb")
        prepare_tables(conn, backend="duckdb")
        tables = {r[0].lower() for r in conn.execute("SHOW TABLES").fetchall()}
        assert {
            "securities",
            "prices",
            "providers",
            "provider_symbols",
            "prices_candidates",
        } <= tables

        # New schema uses an INTEGER primary key and a separate hash for replay deduplication.
        columns = {
            str(row[1]).lower(): str(row[2]).upper()
            for row in conn.execute('PRAGMA table_info("PRICES_CANDIDATES")').fetchall()
        }
        assert columns["candidate_id"] == "BIGINT"
        assert columns["content_hash"].startswith("VARCHAR")

        conn.execute("INSERT INTO securities (asset_id,symbol) VALUES (1001,'PZU.WA')")
        conn.execute(
            "INSERT INTO providers (provider_id,provider_code,name,priority) "
            "VALUES (1,'YFINANCE','Yahoo Finance',10)"
        )
        conn.execute(
            "INSERT INTO provider_symbols "
            "(provider_symbol_id,asset_id,provider_id,provider_symbol,valid_from) "
            "VALUES (1,1001,1,'PZU.WA','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO prices_candidates "
            "(candidate_id,content_hash,asset_id,provider_id,provider_symbol_id,"
            "symbol,date,open,high,low,close,volume) "
            "VALUES (1,?,?,?,?,?,'2026-11-16',60.01,62.01,59.01,61.01,123456)",
            ("a" * 64, 1001, 1, 1, "PZU.WA"),
        )
        row = conn.execute(
            "SELECT candidate_id,asset_id,close,volume,content_hash FROM prices_candidates"
        ).fetchone()
        assert row == (1, 1001, Decimal("61.01000000"), Decimal("123456"), "a" * 64)

        # The hash must not be confused with the integer primary key.
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO prices_candidates "
                "(candidate_id,content_hash,asset_id,provider_id,provider_symbol_id,symbol,date,close) "
                "VALUES (2,?,1001,1,1,'PZU.WA','2026-11-16',61)",
                ("a" * 64,),
            )

        # A genuinely invalid ASSET_ID must still violate the FK.
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO prices_candidates "
                "(candidate_id,content_hash,asset_id,provider_id,provider_symbol_id,symbol,date,close) "
                "VALUES (3,?,99999,1,1,'PZU.WA','2026-11-16',61)",
                ("b" * 64,),
            )
    finally:
        conn.close()


def test_existing_legacy_prices_backfilled_safely(tmp_path):
    conn = duckdb.connect(str(tmp_path / "legacy.duckdb"))
    try:
        conn.execute("CREATE TABLE securities (asset_id BIGINT, symbol VARCHAR)")
        conn.execute("CREATE TABLE prices (symbol VARCHAR, date TIMESTAMP, close DOUBLE)")
        conn.execute("INSERT INTO securities VALUES (7,'PZU.WA')")
        conn.execute(
            "INSERT INTO prices VALUES ('PZU.WA','2026-11-16',61.25),('UNKNOWN.WA','2026-11-16',10)"
        )
        prepare_tables(conn, backend="duckdb")
        assert conn.execute("SELECT asset_id FROM prices WHERE symbol='PZU.WA'").fetchone()[0] == 7
        assert (
            conn.execute("SELECT asset_id FROM prices WHERE symbol='UNKNOWN.WA'").fetchone()[0]
            is None
        )
        assert diagnostics(conn)["unmapped_prices"] == 1
        prepare_tables(conn, backend="duckdb")
        assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    finally:
        conn.close()


def test_duplicate_asset_ids_refused(tmp_path):
    conn = duckdb.connect(str(tmp_path / "duplicate.duckdb"))
    try:
        conn.execute("CREATE TABLE securities (asset_id BIGINT, symbol VARCHAR)")
        conn.execute("INSERT INTO securities VALUES (1,'X'),(1,'Y')")
        with pytest.raises(ValueError, match="non-null and unique"):
            prepare_tables(conn, backend="duckdb")
    finally:
        conn.close()
