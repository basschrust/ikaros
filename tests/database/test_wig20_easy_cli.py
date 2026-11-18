"""WIG20 one-command interface tests. Does not require market access."""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from run import ingest_gpw_prices as cli


def test_five_completed_sessions_skip_weekends_and_exchange_holiday() -> None:
    # 11 November 2026 was a GPW holiday, not an eligible session.
    assert cli.last_gpw_sessions(date(2026, 11, 17), 5) == (
        date(2026, 11, 10),
        date(2026, 11, 12),
        date(2026, 11, 13),
        date(2026, 11, 16),
        date(2026, 11, 17),
    )


def test_rolls_back_through_new_year_and_crosses_2027_calendar() -> None:
    assert cli.last_gpw_sessions(date(2027, 1, 4), 3) == (
        date(2026, 12, 29),
        date(2026, 12, 30),
        date(2027, 1, 4),
    )


@pytest.mark.parametrize("count", (0, -1, 101))
def test_invalid_session_count_fails(count: int) -> None:
    with pytest.raises(ValueError, match="last-sessions"):
        cli.last_gpw_sessions(date(2026, 11, 17), count)


def test_unknown_calendar_year_fails_closed() -> None:
    with pytest.raises(ValueError, match="calendar unavailable"):
        cli.last_gpw_sessions(date(2030, 1, 2), 5)


def test_read_wig20_csv_without_alias_duplication(tmp_path: Path) -> None:
    path = tmp_path / "wig20.csv"
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output, fieldnames=("canonical_symbol", "venue", "provider_code", "provider_symbol")
        )
        writer.writeheader()
        for n in range(20):
            for provider in ("YFINANCE", "BIZNESRADAR", "STOOQ"):
                writer.writerow(
                    {
                        "canonical_symbol": f"T{n:02d}.WA",
                        "venue": "GPW",
                        "provider_code": provider,
                        "provider_symbol": f"T{n:02d}",
                    }
                )
    assert cli.wig20_symbols(path) == [f"T{n:02d}.WA" for n in range(20)]


def test_csv_rejects_missing_constituents(tmp_path: Path) -> None:
    path = tmp_path / "wig20.csv"
    path.write_text("canonical_symbol,venue\nPZU.WA,GPW\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 20"):
        cli.wig20_symbols(path)


def _fake_dependencies(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen = {}
    package = ModuleType("config")
    package.__path__ = []
    cfg = ModuleType("config.config")
    cfg.load_config = lambda _name: {"database": {"driver": "snowflake"}}
    factory = ModuleType("database.repositories.factory")

    class Repo:
        connection = object()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    factory.repository_from_config = lambda _cfg: Repo()
    provider = ModuleType("ingestion.gpw_provider_feeds")
    provider.default_feeds = lambda: {}
    pipeline = ModuleType("ingestion.gpw_multi_source_job")

    def run_job(_connection, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            symbols_processed=20,
            provider_requests=60,
            days_seen=100,
            candidates_inserted=100,
            promoted=100,
            unresolved=0,
            preserved_conflicts=0,
            provider_errors=(),
        )

    pipeline.run_job = run_job
    monkeypatch.setitem(sys.modules, "config", package)
    monkeypatch.setitem(sys.modules, "config.config", cfg)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory)
    monkeypatch.setitem(sys.modules, "ingestion.gpw_provider_feeds", provider)
    monkeypatch.setitem(sys.modules, "ingestion.gpw_multi_source_job", pipeline)
    return seen


def test_one_command_fetches_twenty_for_five_sessions_and_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_dependencies(monkeypatch)
    monkeypatch.setattr(cli, "_warsaw_yesterday", lambda: date(2026, 11, 17))
    monkeypatch.setattr(cli, "wig20_symbols", lambda: [f"T{n:02d}.WA" for n in range(20)])
    assert cli.main(["--wig20", "--write"]) == 0
    assert seen["symbols"] == [f"T{n:02d}.WA" for n in range(20)]
    assert (seen["start"], seen["end"]) == (date(2026, 11, 10), date(2026, 11, 17))
    assert seen["write"] is True
    assert seen["minimum_sources"] == 1


def test_one_command_can_dry_run_or_change_count(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_dependencies(monkeypatch)
    monkeypatch.setattr(cli, "_warsaw_yesterday", lambda: date(2026, 11, 17))
    monkeypatch.setattr(cli, "wig20_symbols", lambda: [f"T{n:02d}.WA" for n in range(20)])
    assert cli.main(["--wig20", "--last-sessions", "2"]) == 0
    assert (seen["start"], seen["end"]) == (date(2026, 11, 16), date(2026, 11, 17))
    assert seen["write"] is False


def test_previous_explicit_symbol_mode_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_dependencies(monkeypatch)
    monkeypatch.setattr(cli, "_warsaw_yesterday", lambda: date(2026, 11, 17))
    assert cli.main(["--symbols", "PZU.WA", "--start", "2026-11-16", "--end", "2026-11-17"]) == 0
    assert seen["symbols"] == ["PZU.WA"]
    assert (seen["start"], seen["end"]) == (date(2026, 11, 16), date(2026, 11, 17))


def test_conflicting_modes_fail_before_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_warsaw_yesterday", lambda: date(2026, 11, 17))
    with pytest.raises(SystemExit):
        cli.main(["--wig20", "--last-sessions", "0", "--write"])
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--wig20",
                "--last-sessions",
                "5",
                "--start",
                "2026-11-10",
                "--end",
                "2026-11-17",
                "--write",
            ]
        )
