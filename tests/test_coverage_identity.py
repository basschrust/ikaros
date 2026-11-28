"""Fail-closed metadata and additive VENUE migration; no provider/network calls."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database.coverage_identity import (
    COVERAGE_FIELDS,
    derivative_identities,
    index_identity,
    stock_identities,
)
from database.daedalus_reference import ensure_reference_schema
from database.local_bootstrap import _add_contracts
from run.import_local_daedalus_inputs import (
    CONTRACT_FIELDS,
    read_csv,
    store_contracts,
    validate_contracts,
)
from providers.derivatives.wig20_listings import ListedContract

ROOT = Path(__file__).resolve().parents[1]


def _reference_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """CREATE TABLE "SECURITIES" ("SYMBOL" TEXT, "VENUE" TEXT, "INSTRUMENT_CLASS" TEXT);
           CREATE TABLE "DERIVATIVE_CONTRACTS" ("CONTRACT_ID" TEXT,
             "VENUE" TEXT, "INSTRUMENT_TYPE" TEXT);"""
    )
    return db


def test_coverage_columns_separate_venue_from_instrument_type() -> None:
    assert COVERAGE_FIELDS[:3] == ("venue", "instrument_type", "symbol")
    assert "dataset" not in COVERAGE_FIELDS
    assert index_identity(symbol="WIG20").venue == "GPW"
    assert index_identity(symbol="WIG20").instrument_type == "INDEX"
    with pytest.raises(ValueError, match="market association"):
        index_identity(symbol="SPX")


def test_stock_venue_comes_from_security_reference_not_pipeline() -> None:
    db = _reference_db()
    db.executemany(
        'INSERT INTO "SECURITIES" VALUES (?,?,?)',
        [("PZU.WA", "GPW", "stock"), ("AAPL", "NASDAQ", "equity")],
    )
    found = stock_identities(connection=db, symbols=("PZU.WA", "AAPL"))
    assert (found["PZU.WA"].venue, found["PZU.WA"].instrument_type) == ("GPW", "EQUITY")
    assert (found["AAPL"].venue, found["AAPL"].instrument_type) == ("NASDAQ", "EQUITY")


def test_stock_duplicate_missing_or_unrecognized_type_rejected() -> None:
    db = _reference_db()
    db.execute('INSERT INTO "SECURITIES" VALUES (?,?,?)', ("PZU.WA", "GPW", "stock"))
    with pytest.raises(ValueError, match="Missing stock"):
        stock_identities(connection=db, symbols=("MISSING.WA",))
    db.execute('INSERT INTO "SECURITIES" VALUES (?,?,?)', ("PZU.WA", "GPW", "stock"))
    with pytest.raises(ValueError, match="Ambiguous"):
        stock_identities(connection=db, symbols=("PZU.WA",))
    db.execute('DELETE FROM "SECURITIES"')
    db.execute('INSERT INTO "SECURITIES" VALUES (?,?,?)', ("PZU.WA", "GPW", "bond"))
    with pytest.raises(ValueError, match="Expected stock"):
        stock_identities(connection=db, symbols=("PZU.WA",))


def test_derivative_types_independent_of_venues_and_missing_remains_unknown() -> None:
    db = _reference_db()
    db.executemany(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?)',
        [
            ("OW20L264250", "GPW", "OPTION"),
            ("FW20Z2620", "GPW", "FUTURE"),
            ("OTHER_OPT", "CBOE", "OPTION"),
            ("LEGACY", None, "OPTION"),
        ],
    )
    found = derivative_identities(
        connection=db, symbols=("OW20L264250", "FW20Z2620", "OTHER_OPT", "LEGACY")
    )
    assert (found["OW20L264250"].venue, found["OW20L264250"].instrument_type) == ("GPW", "OPTION")
    assert (found["FW20Z2620"].venue, found["FW20Z2620"].instrument_type) == ("GPW", "FUTURE")
    assert (found["OTHER_OPT"].venue, found["OTHER_OPT"].instrument_type) == ("CBOE", "OPTION")
    assert found["LEGACY"].venue is None


def test_derivative_missing_and_duplicate_fail_closed() -> None:
    db = _reference_db()
    with pytest.raises(ValueError, match="Missing contract"):
        derivative_identities(connection=db, symbols=("UNKNOWN",))
    db.executemany(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?)',
        [("DUP", "GPW", "OPTION"), ("DUP", "CBOE", "OPTION")],
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        derivative_identities(connection=db, symbols=("DUP",))


def test_listings_and_reviewed_csvs_carry_explicit_gpw_venue() -> None:
    row = ListedContract(
        "FW20Z2620",
        "FUTURE",
        date(2026, 12, 18),
        None,
        None,
        ("https://www.bankier.pl",),
        None,
    ).manifest_row()
    assert row["venue"] == "GPW"
    for name in (
        "wig20_gpw_verified_contracts_2026_10.csv",
        "wig20_gpw_verified_options_extra.csv",
    ):
        rows = read_csv(path=ROOT / "data" / name, fields=CONTRACT_FIELDS)
        assert all(rec["venue"] == "GPW" for rec in rows)
        assert all(rec["venue"] == "GPW" for rec in validate_contracts(rows=rows))
        bad = [dict(item) for item in rows]
        bad[0]["venue"] = "CBOE"
        with pytest.raises(ValueError, match="venue must be GPW"):
            validate_contracts(rows=bad)


class SQLiteReference:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            'CREATE TABLE "DERIVATIVE_CONTRACTS" ('
            '"CONTRACT_ID" TEXT PRIMARY KEY, "INSTRUMENT_TYPE" TEXT, '
            '"UNDERLYING_SYMBOL" TEXT, "EXPIRY_DATE" DATE)'
        )

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        if "information_schema.columns" in sql:
            return self.db.execute('SELECT name FROM pragma_table_info("DERIVATIVE_CONTRACTS")')
        adapted = tuple(
            value.isoformat()
            if isinstance(value, date)
            else str(value)
            if isinstance(value, Decimal)
            else value
            for value in params
        )
        return self.db.execute(sql, adapted)


def test_additive_schema_migration_keeps_unknown_venues_unknown() -> None:
    conn = SQLiteReference()
    conn.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?,?)',
        ("LEGACY", "OPTION", "WIG20", "2026-12-18"),
    )
    ensure_reference_schema(conn)
    ensure_reference_schema(conn)
    assert conn.execute(
        'SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=?', ("LEGACY",)
    ).fetchone() == (None,)
    rows = validate_contracts(
        rows=read_csv(
            path=ROOT / "data" / "wig20_gpw_verified_contracts_2026_10.csv", fields=CONTRACT_FIELDS
        )
    )
    assert store_contracts(connection=conn, items=rows) == 14
    assert store_contracts(connection=conn, items=rows) == 0
    assert conn.execute(
        'SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=?', ("FW20Z2620",)
    ).fetchone() == ("GPW",)


def test_conflicting_contract_venue_does_not_get_overwritten() -> None:
    conn = SQLiteReference()
    ensure_reference_schema(conn)
    conn.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" '
        '("CONTRACT_ID", "INSTRUMENT_TYPE", "UNDERLYING_SYMBOL", "EXPIRY_DATE", "VENUE") '
        "VALUES (?,?,?,?,?)",
        ("FW20Z2620", "FUTURE", "WIG20", "2026-12-18", "CBOE"),
    )
    row = validate_contracts(
        rows=read_csv(
            path=ROOT / "data" / "wig20_gpw_verified_contracts_2026_10.csv", fields=CONTRACT_FIELDS
        )
    )[0]
    with pytest.raises(ValueError, match="Conflicting FW20Z2620.VENUE"):
        store_contracts(connection=conn, items=[row])


def test_legacy_registration_compatible_and_reviewed_venue_enriches_existing(
    tmp_path: Path,
) -> None:
    # The bootstrap accepts older user CSVs but deliberately stores NULL venue.
    # The date column is SQLite TEXT here to avoid SQLite's non-date CAST semantics.
    class Cursor:
        def __init__(self, cur: sqlite3.Cursor):
            self.inner = cur

        def __enter__(self):
            return self.inner

        def __exit__(self, *_args):
            self.inner.close()

    class DB:
        def __init__(self):
            self.db = sqlite3.connect(":memory:")
            self.db.execute(
                'CREATE TABLE "DERIVATIVE_CONTRACTS" ('
                '"CONTRACT_ID" TEXT, "INSTRUMENT_TYPE" TEXT, '
                '"UNDERLYING_SYMBOL" TEXT, "EXPIRY_DATE" TEXT, "VENUE" TEXT)'
            )

        def cursor(self):
            return Cursor(self.db.cursor())

    db = DB()
    db.db.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?,?,?)',
        ("LEGACY", "OPTION", "WIG20", "2026-12-18", None),
    )
    legacy_csv = tmp_path / "legacy.csv"
    legacy_csv.write_text(
        "contract_id,instrument_type,underlying_symbol,expiry_date\n"
        "LEGACY,OPTION,WIG20,2026-12-18\n",
        encoding="utf-8",
    )
    assert _add_contracts(db, path=legacy_csv) == 0
    assert db.db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone()[0] is None
    with_venue = tmp_path / "venue.csv"
    with_venue.write_text(
        "contract_id,venue,instrument_type,underlying_symbol,expiry_date\n"
        "LEGACY,GPW,OPTION,WIG20,2026-12-18\n",
        encoding="utf-8",
    )
    assert _add_contracts(db, path=with_venue) == 0
    assert db.db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone()[0] == "GPW"


def test_legacy_coverage_reclassification_keeps_all_other_fields() -> None:
    from database.coverage_identity import reclassify_existing_coverage

    db = _reference_db()
    db.execute('INSERT INTO "SECURITIES" VALUES (?,?,?)', ("PZU.WA", "GPW", "stock"))
    db.executemany(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?)',
        [("FW20Z2620", "GPW", "FUTURE"), ("OW20L264250", None, "OPTION")],
    )
    rows = [
        {"dataset": "GPW", "symbol": "PZU.WA", "status": "OK", "accepted_sessions": "100"},
        {"dataset": "WIG20", "symbol": "WIG20", "status": "OK", "accepted_sessions": "100"},
        {
            "dataset": "FUTURE",
            "symbol": "FW20Z2620",
            "status": "NO_SOURCE_ROWS",
            "listing": "DISCOVERED",
        },
        {
            "dataset": "OPTIONS",
            "symbol": "OW20L264250",
            "status": "SOURCE_ERROR",
            "source_errors": "provider failed",
        },
    ]
    converted, unknown = reclassify_existing_coverage(connection=db, rows=rows)
    assert [(r["venue"], r["instrument_type"]) for r in converted] == [
        ("GPW", "EQUITY"),
        ("GPW", "INDEX"),
        ("GPW", "FUTURE"),
        ("", "OPTION"),
    ]
    assert unknown == ("OW20L264250",)
    assert converted[2]["listing"] == "DISCOVERED"
    assert converted[3]["source_errors"] == "provider failed"
    assert all("dataset" not in record for record in converted)


def test_legacy_coverage_reclassification_rejects_misclassified_type() -> None:
    from database.coverage_identity import reclassify_existing_coverage

    db = _reference_db()
    db.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?)', ("OW20L264250", "GPW", "OPTION")
    )
    with pytest.raises(ValueError, match="Conflicting legacy"):
        reclassify_existing_coverage(
            connection=db, rows=[{"dataset": "FUTURE", "symbol": "OW20L264250"}]
        )


def test_legacy_duckdb_contract_table_migrates_without_rewriting_rows(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    from database.local_duckdb import LocalDuckDBRepository

    path = tmp_path / "ikaros.duckdb"
    raw = duckdb.connect(str(path))
    try:
        raw.execute(
            'CREATE TABLE "DERIVATIVE_CONTRACTS" ('
            '"CONTRACT_ID" VARCHAR PRIMARY KEY, "INSTRUMENT_TYPE" VARCHAR NOT NULL, '
            '"UNDERLYING_SYMBOL" VARCHAR, "EXPIRY_DATE" DATE NOT NULL)'
        )
        raw.execute(
            'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?,?)',
            ("FW20Z2620", "FUTURE", "WIG20", "2026-12-18"),
        )
    finally:
        raw.close()
    with LocalDuckDBRepository(output_dir=tmp_path) as repo:
        conn = repo.connection
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE lower(table_name)='derivative_contracts'"
            ).fetchall()
        }
        assert "VENUE" in columns
        assert conn.execute(
            'SELECT "VENUE", "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS"'
        ).fetchone() == (None, "FW20Z2620")


def test_reclassify_cli_writes_new_file_without_overwriting_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csv
    import sys
    from types import ModuleType

    from run.reclassify_coverage import main

    db = _reference_db()
    db.execute('INSERT INTO "SECURITIES" VALUES (?,?,?)', ("PZU.WA", "GPW", "stock"))
    db.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?)', ("OW20L264250", "GPW", "OPTION")
    )

    source = tmp_path / "coverage.csv"
    source.write_text(
        "DATASET,SYMBOL,STATUS,ACCEPTED_SESSIONS,LISTING\n"
        "GPW,PZU.WA,OK,12,\n"
        "OPTION,OW20L264250,NO_SOURCE_ROWS,0,DISCOVERED\n",
        encoding="utf-8",
    )
    config = ModuleType("config.config")
    config.PROJECT_ROOT = tmp_path
    config.load_config = lambda _name: {"database": {"driver": "duckdb"}}
    factory = ModuleType("database.repositories.factory")

    class Repo:
        connection = db

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    factory.repository_from_config = lambda _cfg: Repo()
    monkeypatch.setitem(sys.modules, "config.config", config)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory)
    assert main(argv=["--source", str(source), "--write"]) == 0
    assert source.read_text(encoding="utf-8").startswith("DATASET,SYMBOL,STATUS")
    target = tmp_path / "coverage_venue.csv"
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["venue"], r["instrument_type"]) for r in rows] == [
        ("GPW", "EQUITY"),
        ("GPW", "OPTION"),
    ]
    assert rows[1]["listing"] == "DISCOVERED"
    # A repeated write must refuse overwriting a previously inspected output.
    assert main(argv=["--source", str(source), "--write"]) == 1
