"""Offline tests for the zero-Snowflake local derivative registry workflow."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from run.setup_local_duckdb import resolve_contracts_csv


LEGACY_HEADER = ["contract_id", "instrument_type", "underlying_symbol", "expiry_date"]
VENUE_HEADER = ["contract_id", "venue", "instrument_type", "underlying_symbol", "expiry_date"]


def test_manifest_is_optional_on_first_boot(tmp_path: Path) -> None:
    assert resolve_contracts_csv(project_root=tmp_path, requested=None) is None


def test_standard_manifest_is_detected_without_extra_flags(tmp_path: Path) -> None:
    location = tmp_path / "data" / "derivative_contracts.csv"
    location.parent.mkdir()
    location.write_text(",".join(LEGACY_HEADER) + "\nFW20Z2620,FUTURE,WIG20,2026-12-18\n")
    assert resolve_contracts_csv(project_root=tmp_path, requested=None) == location


def test_explicit_override_and_missing_path(tmp_path: Path) -> None:
    custom = tmp_path / "contracts.csv"
    custom.write_text(",".join(LEGACY_HEADER) + "\n")
    assert resolve_contracts_csv(project_root=tmp_path, requested=Path("contracts.csv")) == custom
    with pytest.raises(FileNotFoundError, match="Contract manifest not found"):
        resolve_contracts_csv(project_root=tmp_path, requested=Path("missing.csv"))


def test_example_is_reviewable_not_automatically_loaded() -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "data" / "derivative_contracts.example.csv"
    with example.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) >= 2
    assert set(rows[0]) == set(VENUE_HEADER)
    assert {r["venue"] for r in rows} == {"GPW"}
    assert {r["instrument_type"] for r in rows} == {"FUTURE", "OPTION"}
    assert len({r["contract_id"] for r in rows}) == len(rows)
    for row in rows:
        assert row["contract_id"].startswith(("F", "O"))
        assert date.fromisoformat(row["expiry_date"]).year >= 2026
        assert row["underlying_symbol"]


def test_snowflake_import_is_typing_only() -> None:
    # No runtime Snowflake import is allowed by the BiznesRadar fetch adapter.
    import ast

    path = Path(__file__).resolve().parents[1] / "ikaros_app" / "derivative_prices.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "snowflake.connector"
        for node in module.body
    )


def test_setup_passes_auto_detected_csv_into_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A newcomer need not know the --contracts-csv flag."""
    from types import ModuleType
    import sys

    root = tmp_path
    manifest = root / "data" / "derivative_contracts.csv"
    manifest.parent.mkdir()
    manifest.write_text(",".join(LEGACY_HEADER) + "\nFW20Z2620,FUTURE,WIG20,2026-12-18\n")

    configuration = ModuleType("config.config")
    configuration.PROJECT_ROOT = root
    configuration.load_config = lambda _name: {"database": {"driver": "duckdb"}}
    bootstrapping = ModuleType("database.local_bootstrap")
    calls: list[dict] = []

    def fake_bootstrap(_connection: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return type("Report", (), {"missing_files": (), "derivative_contracts": 1})()

    bootstrapping.bootstrap = fake_bootstrap
    factory = ModuleType("database.repositories.factory")

    class FakeRepo:
        connection = object()

        def __enter__(self) -> "FakeRepo":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    factory.repository_from_config = lambda _config: FakeRepo()
    monkeypatch.setitem(sys.modules, "config.config", configuration)
    monkeypatch.setitem(sys.modules, "database.local_bootstrap", bootstrapping)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory)

    from run.setup_local_duckdb import main

    assert main(["--write"]) == 0
    assert len(calls) == 1
    assert calls[0]["contracts_csv"] == manifest
    assert calls[0]["write"] is True
    assert "Derivative provider mappings are seeded automatically" in capsys.readouterr().out
