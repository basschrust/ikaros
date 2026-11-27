"""Offline starter smoke tests; no providers or cloud credentials required."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from run.prepare_daedalus import default_market_dir, missing_ranges, report

ROOT = Path(__file__).resolve().parents[1]


def records(name: str) -> list[dict[str, str]]:
    with (ROOT / "data" / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_bundled_snapshot() -> None:
    weights = records("wig20_gpw_official_sept2026.csv")
    stocks = records("gpw_stocks.wig20.csv")
    assert len(weights) == len(stocks) == 20
    assert sum(Decimal(row["weight_pct"]) for row in weights) == 100
    assert {row["security_symbol"] for row in weights} == {row["ticker"] + ".WA" for row in stocks}
    assert {row["as_of"] for row in weights} == {"2026-09-21"}
    assert {row["security_symbol"] for row in weights} >= {"PZU.WA", "EBP.WA", "MDV.WA"}
    assert {row["canonical_symbol"] for row in records("provider_symbols_gpw_wig20.csv")} == {
        row["security_symbol"] for row in weights
    }


def test_contract_manifest() -> None:
    contracts = records("wig20_gpw_verified_contracts_2026_10.csv") + records(
        "wig20_gpw_verified_options_extra.csv"
    )
    assert len({row["contract_id"] for row in contracts}) == len(contracts) == 24
    futures = [row for row in contracts if row["instrument_type"] == "FUTURE"]
    options = [row for row in contracts if row["instrument_type"] == "OPTION"]
    assert len(futures) == 4
    assert {row["multiplier"] for row in futures} == {"20"}
    assert len(options) == 20
    assert {row["option_side"] for row in options} == {"CALL", "PUT"}
    assert {row["multiplier"] for row in options} == {"10"}
    assert all(Decimal(row["strike"]) > 0 for row in options)
    assert all(row["source"].startswith("https://") for row in contracts)


def test_path_detection(tmp_path: Path) -> None:
    root = tmp_path / "ikaros"
    root.mkdir()
    assert default_market_dir(root=root) == root / "output" / "market_data"
    daedalus = tmp_path / "Daedalus" / "input"
    daedalus.mkdir(parents=True)
    (daedalus / "wig20.yaml").touch()
    assert default_market_dir(root=root) == daedalus / "market_data"
    other = tmp_path / "pymarketsim" / "input"
    other.mkdir(parents=True)
    (other / "wig20.yaml").touch()
    assert default_market_dir(root=root) == root / "output" / "market_data"


def test_report_never_claims_success_with_missing_data(capsys: pytest.CaptureFixture) -> None:
    assert report(problems=["PZU missing"], status=0) == 1
    assert "NOT READY" in capsys.readouterr().out
    assert report(problems=[], status=1) == 1
    assert "NOT READY" in capsys.readouterr().out
    assert report(problems=[], status=0) == 0
    assert "CHECK PASSED" in capsys.readouterr().out


def test_reference_seed_idempotent_in_real_duckdb(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    from database.daedalus_readiness import check
    from database.daedalus_starter import seed
    from database.local_duckdb import LocalDuckDBRepository

    with LocalDuckDBRepository(output_dir=tmp_path) as repo:
        conn = repo.connection
        target = date(2026, 11, 25)
        first = seed(connection=conn, project_root=ROOT, target=target)
        second = seed(connection=conn, project_root=ROOT, target=target)
        assert first == second
        assert len(first) == 20
        assert conn.execute('SELECT COUNT(*) FROM "DERIVATIVE_CONTRACTS"').fetchone()[0] == 24
        assert conn.execute('SELECT COUNT(*) FROM "INDEX_CONSTITUENTS"').fetchone()[0] == 20
        assert conn.execute('SELECT COUNT(*) FROM "PRICES"').fetchone()[0] == 0
        problems = check(connection=conn, target=target, recent_sessions=(target,))
        assert problems
        assert any("historical sessions" in item for item in problems)


def test_mdv_biznesradar_mapping_covers_rename_without_overlap() -> None:
    mappings = [
        row
        for row in records("provider_symbols_gpw_wig20.csv")
        if row["canonical_symbol"] == "MDV.WA" and row["provider_code"] == "BIZNESRADAR"
    ]
    assert len(mappings) == 2
    assert mappings[0]["provider_symbol"] == mappings[1]["provider_symbol"] == "MDV"
    assert mappings[0]["valid_from"] == "2026-02-19"
    assert mappings[0]["valid_to"] == "2026-09-20"
    assert mappings[1]["valid_from"] == "2026-09-21"
    assert mappings[1]["valid_to"] == ""


def test_missing_ranges_targets_only_gaps() -> None:
    day = date(2026, 2, 19)
    assert missing_ranges(missing=set()) == []
    assert missing_ranges(missing={day}) == [(day, day)]
    assert missing_ranges(
        missing={day, date(2026, 2, 20), date(2026, 2, 23), date(2026, 11, 25)}
    ) == [(day, date(2026, 2, 23)), (date(2026, 11, 25), date(2026, 11, 25))]
