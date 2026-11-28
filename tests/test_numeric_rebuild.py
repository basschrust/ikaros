"""Regression: referenced SECURITIES requires a non-destructive rebuild."""

from pathlib import Path
from decimal import Decimal

import pytest

from database.numeric_contract import Column
from database.numeric_migration import (
    UnsafeNumericMigration,
    inspect,
    rebuild,
    rewrite_export_schema,
)


SCHEMA = """CREATE SEQUENCE "main"."PRICES_CANDIDATES_ID_SEQ" START 1;
CREATE TABLE "main"."SECURITIES" ("ASSET_ID" BIGINT PRIMARY KEY,
  "REFERENCE_PRICE" DOUBLE, "SYMBOL" VARCHAR);
CREATE TABLE "main"."PROVIDER_SYMBOLS" ("PROVIDER_SYMBOL_ID" BIGINT PRIMARY KEY,
  "ASSET_ID" BIGINT REFERENCES "main"."SECURITIES"("ASSET_ID"));
CREATE TABLE "main"."PRICES" ("SYMBOL" VARCHAR, "DIVIDEND" DOUBLE,
  "SPLIT_RATIO" DOUBLE);
CREATE TABLE "main"."FX_RATES" ("SYMBOL" VARCHAR,
  "OPEN" DECIMAL(24,8), "CLOSE" DECIMAL(24,8));
"""


def test_edits_only_named_column_types_and_retains_keys() -> None:
    changes = [
        Column("SECURITIES", "REFERENCE_PRICE", "DOUBLE", "DECIMAL(24,8)"),
        Column("PRICES", "DIVIDEND", "DOUBLE", "DECIMAL(24,8)"),
        Column("PRICES", "SPLIT_RATIO", "DOUBLE", "DECIMAL(24,8)"),
        Column("FX_RATES", "OPEN", "DECIMAL(24,8)", "DECIMAL(24,10)"),
        Column("FX_RATES", "CLOSE", "DECIMAL(24,8)", "DECIMAL(24,10)"),
    ]
    sql = rewrite_export_schema(SCHEMA, changes)
    assert '"REFERENCE_PRICE" DECIMAL(24,8)' in sql
    assert '"OPEN" DECIMAL(24,10)' in sql
    assert '"CLOSE" DECIMAL(24,10)' in sql
    assert '"DIVIDEND" DECIMAL(24,8)' in sql
    assert '"SPLIT_RATIO" DECIMAL(24,8)' in sql
    assert '"ASSET_ID" BIGINT PRIMARY KEY' in sql
    assert 'REFERENCES "main"."SECURITIES"("ASSET_ID")' in sql
    assert 'CREATE SEQUENCE "main"."PRICES_CANDIDATES_ID_SEQ" START 1' in sql
    assert sql.count('"REFERENCE_PRICE"') == 1
    assert '"PRICES" ("SYMBOL" VARCHAR' in sql


def test_refuses_missing_or_changed_column_declaration() -> None:
    with pytest.raises(UnsafeNumericMigration, match="found 0"):
        rewrite_export_schema(
            SCHEMA, [Column("SECURITIES", "REFERENCE_PRICE", "FLOAT", "DECIMAL(24,8)")]
        )
    with pytest.raises(UnsafeNumericMigration, match="Missing exported"):
        rewrite_export_schema(SCHEMA, [Column("SOME_TABLE", "X", "DOUBLE", "DECIMAL(24,8)")])


def test_refuses_duplicate_conversion() -> None:
    item = Column("SECURITIES", "REFERENCE_PRICE", "DOUBLE", "DECIMAL(24,8)")
    with pytest.raises(UnsafeNumericMigration, match="Duplicate"):
        rewrite_export_schema(SCHEMA, [item, item])


def test_rebuild_depends_on_new_output_filename(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "ikaros.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(
            'CREATE TABLE "SECURITIES" ("ASSET_ID" BIGINT PRIMARY KEY, "REFERENCE_PRICE" DOUBLE)'
        )
        con.execute(
            'CREATE TABLE "PROVIDER_SYMBOLS" ("ID" BIGINT, "ASSET_ID" BIGINT, FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID"))'
        )
        con.execute('CREATE SEQUENCE "MY_SEQ" START 1')
        con.execute('INSERT INTO "SECURITIES" VALUES (11, NULL)')
        con.execute('INSERT INTO "PROVIDER_SYMBOLS" VALUES (1, 11)')
        con.execute('CREATE TABLE "PRICES" ("CLOSE" DOUBLE)')
        con.execute('INSERT INTO "PRICES" VALUES (12.125)')
    # Old in-place command no longer generates a backup then fails late.
    with pytest.raises(UnsafeNumericMigration, match="--rebuild"):
        inspect(path, write=True)
    assert not list(tmp_path.glob("*.before_decimal_*.duckdb"))
    converted = tmp_path / "ikaros_verified.duckdb"
    assert rebuild(path, output=converted) == 0
    assert converted.is_file()
    with duckdb.connect(str(path), read_only=True) as con:
        assert (
            con.execute('SELECT typeof("REFERENCE_PRICE") FROM "SECURITIES"').fetchone()[0]
            == "DOUBLE"
        )
    with duckdb.connect(str(converted), read_only=True) as con:
        assert (
            con.execute('SELECT typeof("REFERENCE_PRICE") FROM "SECURITIES"').fetchone()[0]
            == "DECIMAL(24,8)"
        )
        assert con.execute('SELECT "CLOSE" FROM "PRICES"').fetchone()[0] == Decimal("12.12500000")
        with pytest.raises(Exception):
            con.execute('INSERT INTO "PROVIDER_SYMBOLS" VALUES (2, 999)')


def test_cannot_rebuild_over_original(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "ikaros.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute('CREATE TABLE "PRICES" ("CLOSE" DOUBLE)')
    with pytest.raises(UnsafeNumericMigration, match="distinct from source"):
        rebuild(path, output=path)


def test_sequence_start_prevents_id_collision() -> None:
    from database.numeric_migration import rewrite_export_sequences

    schema = 'CREATE SEQUENCE "main"."PRICES_CANDIDATES_ID_SEQ" START 1 INCREMENT BY 1;\n'
    fixed = rewrite_export_sequences(schema, {"PRICES_CANDIDATES_ID_SEQ": 805})
    assert '"PRICES_CANDIDATES_ID_SEQ" START WITH 805 INCREMENT BY 1;' in fixed
    with pytest.raises(UnsafeNumericMigration, match="Missing sequence"):
        rewrite_export_sequences(schema, {"UNKNOWN_ID_SEQ": 55})
