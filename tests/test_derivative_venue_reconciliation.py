"""Offline venue repair checks: no invented venue and no price-table writes."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from database.derivative_venue_reconciliation import (
    _reference_url,
    apply_gpw_venue_repair,
    plan_gpw_venue_repair,
    read_discovery,
)


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        'CREATE TABLE "DERIVATIVE_CONTRACTS" ('
        '"CONTRACT_ID" TEXT PRIMARY KEY, "VENUE" TEXT, "INSTRUMENT_TYPE" TEXT, '
        '"UNDERLYING_SYMBOL" TEXT, "EXPIRY_DATE" TEXT, '
        '"STRIKE" NUMERIC, "OPTION_SIDE" TEXT, "SOURCE" TEXT)'
    )
    return connection


def _add(
    connection: sqlite3.Connection,
    *,
    contract_id: str,
    kind: str = "OPTION",
    venue: str | None = None,
    source: str = "",
    underlying: str = "WIG20",
    expiry: str = "2026-12-18",
    strike: int | None = 4250,
    side: str | None = "CALL",
) -> None:
    connection.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?,?,?,?,?,?)',
        (contract_id, venue, kind, underlying, expiry, strike, side, source),
    )


def _manifest_row(*, cid: str = "OW20L264250", source: str = "") -> dict[str, str]:
    return {
        "contract_id": cid,
        "symbol": cid,
        "venue": "GPW",
        "instrument_type": "OPTION",
        "underlying_symbol": "WIG20",
        "expiry_date": "2026-12-18",
        "strike": "4250",
        "option_side": "CALL",
        "source": source,
    }


def test_original_report_problem_is_repaired_by_retained_source() -> None:
    db = _db()
    _add(
        db,
        contract_id="OW20L264250",
        source="https://www.biznesradar.pl/gielda/pochodne_opcje | STANDARD_DERIVED",
    )
    before = db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone()[0]
    assert before is None
    plan = plan_gpw_venue_repair(connection=db, discovered={})
    assert plan.eligible == ("OW20L264250",)
    assert plan.unresolved == ()
    assert plan.source_by_contract["OW20L264250"].endswith("/pochodne_opcje")
    assert db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone() == (None,)
    assert apply_gpw_venue_repair(connection=db, plan=plan) == 1
    assert db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone() == ("GPW",)
    again = plan_gpw_venue_repair(connection=db, discovered={})
    assert again.eligible == ()
    assert again.already_gpw == ("OW20L264250",)
    assert apply_gpw_venue_repair(connection=db, plan=again) == 0


def test_archive_manifest_can_fill_missing_stored_source() -> None:
    db = _db()
    _add(db, contract_id="OW20L264250")
    manifest = {
        "OW20L264250": _manifest_row(
            source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED"
        )
    }
    plan = plan_gpw_venue_repair(connection=db, discovered=manifest)
    assert plan.eligible == ("OW20L264250",)
    assert apply_gpw_venue_repair(connection=db, plan=plan) == 1


def test_legacy_manifest_without_venue_column_is_supported(tmp_path: Path) -> None:
    source = tmp_path / "discovered_contracts.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "contract_id",
                "symbol",
                "instrument_type",
                "underlying_symbol",
                "expiry_date",
                "strike",
                "option_side",
                "source",
                "discovered_as_of",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                k: v
                for k, v in {
                    **_manifest_row(
                        source="https://www.biznesradar.pl/gielda/pochodne_opcje | STANDARD_DERIVED"
                    ),
                    "discovered_as_of": "2026-11-27",
                }.items()
                if k != "venue"
            }
        )
    read = read_discovery(path=source)
    db = _db()
    _add(db, contract_id="OW20L264250")
    assert plan_gpw_venue_repair(connection=db, discovered=read).eligible == ("OW20L264250",)


def test_unverified_source_is_left_unknown_even_for_wig20() -> None:
    db = _db()
    for cid, source in [
        (
            "ONLY_STANDARD",
            "https://bossa.pl/edukacja/kontrakty-opcje/kontrakty-terminowe/standardy | STANDARD_DERIVED",
        ),
        ("GENERIC_SITE", "https://www.bankier.pl/gielda | STANDARD_DERIVED"),
        (
            "WRONG_SITE",
            "https://www.bankier.pl.evil.test/gielda/notowania/opcje | STANDARD_DERIVED",
        ),
        ("OTHER_MARKET", "https://www.cmegroup.com/gielda/notowania/opcje | STANDARD_DERIVED"),
        ("BARE_LISTING", "https://www.bankier.pl/gielda/notowania/opcje"),
    ]:
        _add(db, contract_id=cid, source=source)
    plan = plan_gpw_venue_repair(connection=db, discovered={})
    assert plan.eligible == ()
    assert plan.unresolved == (
        "BARE_LISTING",
        "GENERIC_SITE",
        "ONLY_STANDARD",
        "OTHER_MARKET",
        "WRONG_SITE",
    )
    assert (
        db.execute('SELECT COUNT(*) FROM "DERIVATIVE_CONTRACTS" WHERE "VENUE" IS NULL').fetchone()[
            0
        ]
        == 5
    )


def test_kind_specific_evidence_must_match_derivative_type() -> None:
    assert (
        _reference_url(evidence="https://www.biznesradar.pl/gielda/pochodne_opcje", kind="FUTURE")
        is None
    )
    assert (
        _reference_url(evidence="https://www.bankier.pl/gielda/notowania/futures", kind="OPTION")
        is None
    )
    assert (
        _reference_url(evidence="https://www.bankier.pl/gielda/notowania/futures", kind="FUTURE")
        is not None
    )


def test_discovery_mismatch_blocks_update_without_side_effects() -> None:
    db = _db()
    _add(
        db,
        contract_id="OW20L264250",
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED",
    )
    manifest = _manifest_row(
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED"
    )
    manifest["expiry_date"] = "2027-01-15"
    with pytest.raises(ValueError, match="Conflicting discovery metadata"):
        plan_gpw_venue_repair(connection=db, discovered={"OW20L264250": manifest})
    assert db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone() == (None,)


def test_existing_foreign_venue_not_overwritten() -> None:
    db = _db()
    _add(db, contract_id="OW20L264250", venue="CBOE")
    plan = plan_gpw_venue_repair(connection=db, discovered={})
    assert plan.foreign == ("OW20L264250",)
    assert plan.eligible == ()
    with pytest.raises(ValueError, match="registered on CBOE"):
        plan_gpw_venue_repair(
            connection=db,
            discovered={
                "OW20L264250": _manifest_row(
                    source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED"
                )
            },
        )
    assert db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone() == ("CBOE",)


def test_only_wig20_derivatives_are_considered() -> None:
    db = _db()
    _add(
        db,
        contract_id="OW20L264250",
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED",
    )
    _add(
        db,
        contract_id="AAPL",
        underlying="AAPL",
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED",
    )
    plan = plan_gpw_venue_repair(connection=db, discovered={})
    assert plan.eligible == ("OW20L264250",)
    assert apply_gpw_venue_repair(connection=db, plan=plan) == 1
    assert db.execute(
        'SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=?', ("AAPL",)
    ).fetchone() == (None,)


def test_manifest_must_have_required_fields_and_unique_ids(tmp_path: Path) -> None:
    source = tmp_path / "discovered_contracts.csv"
    source.write_text("contract_id,source\nA,B\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        read_discovery(path=source)
    source.write_text(
        "contract_id,symbol,instrument_type,underlying_symbol,expiry_date,source\n"
        "OW20L264250,OW20L264250,OPTION,WIG20,2026-12-18,x\n"
        "OW20L264250,OW20L264250,OPTION,WIG20,2026-12-18,x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        read_discovery(path=source)


def test_coverage_reclassification_after_repair() -> None:
    from database.coverage_identity import reclassify_existing_coverage

    db = _db()
    db.execute('CREATE TABLE "SECURITIES" ("SYMBOL" TEXT, "VENUE" TEXT, "INSTRUMENT_CLASS" TEXT)')
    _add(
        db,
        contract_id="OW20L264250",
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED",
    )
    original = [{"dataset": "OPTIONS", "symbol": "OW20L264250", "status": "NO_SOURCE_ROWS"}]
    before, _unknown = reclassify_existing_coverage(connection=db, rows=original)
    assert before[0]["venue"] == ""
    apply_gpw_venue_repair(connection=db, plan=plan_gpw_venue_repair(connection=db, discovered={}))
    after, unknown = reclassify_existing_coverage(connection=db, rows=original)
    assert after[0]["venue"] == "GPW"
    assert after[0]["instrument_type"] == "OPTION"
    assert unknown == ()


def test_empty_string_venue_is_repaired() -> None:
    db = _db()
    _add(
        db,
        contract_id="OW20L264250",
        venue="",
        source="https://www.bankier.pl/gielda/notowania/opcje | STANDARD_DERIVED",
    )
    plan = plan_gpw_venue_repair(connection=db, discovered={})
    assert plan.eligible == ("OW20L264250",)
    assert apply_gpw_venue_repair(connection=db, plan=plan) == 1
    assert db.execute('SELECT "VENUE" FROM "DERIVATIVE_CONTRACTS"').fetchone() == ("GPW",)


def test_proof_requires_both_listing_url_and_discovery_marker() -> None:
    db = _db()
    _add(db, contract_id="OW20L264250")
    row = _manifest_row(source="https://www.bankier.pl/gielda/notowania/opcje")
    assert plan_gpw_venue_repair(connection=db, discovered={"OW20L264250": row}).unresolved == (
        "OW20L264250",
    )
    row["source"] += " | STANDARD_DERIVED"
    assert plan_gpw_venue_repair(connection=db, discovered={"OW20L264250": row}).eligible == (
        "OW20L264250",
    )
