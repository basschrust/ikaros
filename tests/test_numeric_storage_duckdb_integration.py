"""Run these with Ikaros's normal DuckDB dependency (no external services)."""

from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from database.numeric_migration import inspect, list_columns


def test_legacy_price_and_fx_are_migrated_with_a_backup(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "ikaros.duckdb"
    with closing(duckdb.connect(str(path))) as con:
        con.execute('CREATE TABLE "PRICES" ("SYMBOL" VARCHAR, "CLOSE" DOUBLE, "VOLUME" DOUBLE)')
        con.execute("INSERT INTO PRICES VALUES ('PZU.WA', 42.375, 123456)")
        con.execute('CREATE TABLE "FX_RATES" ("SYMBOL" VARCHAR, "CLOSE" DOUBLE)')
        con.execute("INSERT INTO FX_RATES VALUES ('EURPLN=X', 4.123456789)")
    assert inspect(path) == 0  # default: read-only audit
    with closing(duckdb.connect(str(path), read_only=True)) as con:
        assert con.execute("SELECT typeof(CLOSE) FROM PRICES").fetchone()[0] == "DOUBLE"
    assert inspect(path, write=True) == 0
    backups = list(tmp_path.glob("ikaros.before_decimal_*.duckdb"))
    assert len(backups) == 1
    with closing(duckdb.connect(str(path), read_only=True)) as con:
        cols = {(c.table, c.name): c.actual for c in list_columns(con)}
        assert cols[("PRICES", "CLOSE")] == "DECIMAL(24,8)"
        assert cols[("PRICES", "VOLUME")] == "DECIMAL(38,0)"
        assert cols[("FX_RATES", "CLOSE")] == "DECIMAL(24,10)"
        assert con.execute("SELECT CLOSE FROM PRICES").fetchone()[0] == Decimal("42.37500000")
        assert con.execute("SELECT VOLUME FROM PRICES").fetchone()[0] == Decimal(123456)
        assert con.execute("SELECT CLOSE FROM FX_RATES").fetchone()[0] == Decimal("4.1234567890")
    assert inspect(path, write=True) == 0  # idempotent, no second backup
    assert len(list(tmp_path.glob("ikaros.before_decimal_*.duckdb"))) == 1


def test_fractional_legacy_volume_prevents_any_change(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "ikaros.duckdb"
    with closing(duckdb.connect(str(path))) as con:
        con.execute('CREATE TABLE "PRICES" ("VOLUME" DOUBLE)')
        con.execute('INSERT INTO "PRICES" VALUES (100.5)')
    assert inspect(path) == 2
    from database.numeric_migration import UnsafeNumericMigration

    with pytest.raises(UnsafeNumericMigration, match="Conversion rejected"):
        inspect(path, write=True)
    assert not list(tmp_path.glob("*.before_decimal_*.duckdb"))
    with closing(duckdb.connect(str(path), read_only=True)) as con:
        assert con.execute("SELECT TYPEOF(VOLUME) FROM PRICES").fetchone()[0] == "DOUBLE"
