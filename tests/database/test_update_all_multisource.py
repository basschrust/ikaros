"""Offline unit and orchestration tests for update_all GPW worker routing."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace

import pytest

from ikaros_app.update import Job
from run import update_all


def job(dataset: str, symbols: tuple[str, ...] = ("PZU.WA",)) -> Job:
    return Job(
        dataset=dataset,
        symbols=symbols,
        start=date(2026, 11, 16),
        end=date(2026, 11, 17),
        argv=(sys.executable, "-m", "run.backfill_equities", "--test-legacy"),
    )


def test_gpw_routes_to_multi_provider_using_existing_window_and_batch() -> None:
    data = job("GPW", ("PZU.WA", "ALE.WA"))
    assert update_all.command_for_job(data) == (
        sys.executable,
        "-m",
        "run.ingest_gpw_prices",
        "--symbols",
        "PZU.WA",
        "ALE.WA",
        "--start",
        "2026-11-16",
        "--end",
        "2026-11-17",
    )
    assert "--all-mapped" not in update_all.command_for_job(data)


@pytest.mark.parametrize("dataset", ["FX-YAHOO", "WIG20", "DERIVATIVES", "US", "RATES", "FW20"])
def test_other_datasets_keep_existing_worker(dataset: str) -> None:
    data = job(dataset)
    assert update_all.command_for_job(data) == data.argv


def test_update_all_write_invokes_multi_source_and_preserves_other_workers(monkeypatch) -> None:
    import config.config
    import database.repositories.factory

    class DummyRepo:
        connection = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        config.config, "load_config", lambda _filename: {"database": {"driver": "snowflake"}}
    )
    monkeypatch.setattr(
        database.repositories.factory, "repository_from_config", lambda _config: DummyRepo()
    )
    monkeypatch.setattr(update_all, "yesterday_warsaw", lambda: date(2026, 11, 18))
    monkeypatch.setattr(update_all, "fetch_inventory", lambda _con, dataset: [dataset])
    monkeypatch.setattr(
        update_all,
        "build_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            jobs=[job("GPW"), job("FX-YAHOO")], skipped=[], target=date(2026, 11, 17)
        ),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(update_all.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert update_all.main(["--datasets", "GPW", "FX", "--write", "--through", "2026-11-17"]) == 0
    assert len(calls) == 2
    assert calls[0][2] == "run.ingest_gpw_prices"
    assert calls[0][-1] == "--write"
    assert calls[1][2] == "run.backfill_equities"  # unchanged test fixture argv
    assert calls[1][-1] == "--write"


def test_update_all_plan_only_does_not_execute(monkeypatch) -> None:
    import config.config
    import database.repositories.factory

    class DummyRepo:
        connection = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        config.config, "load_config", lambda _filename: {"database": {"driver": "snowflake"}}
    )
    monkeypatch.setattr(
        database.repositories.factory, "repository_from_config", lambda _config: DummyRepo()
    )
    monkeypatch.setattr(update_all, "yesterday_warsaw", lambda: date(2026, 11, 18))
    monkeypatch.setattr(update_all, "fetch_inventory", lambda _con, dataset: [dataset])
    monkeypatch.setattr(
        update_all,
        "build_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            jobs=[job("GPW")], skipped=[], target=date(2026, 11, 17)
        ),
    )
    monkeypatch.setattr(
        update_all.subprocess, "call", lambda _cmd: pytest.fail("Dry run executed worker")
    )
    assert update_all.main(["--datasets", "GPW", "--through", "2026-11-17"]) == 0


def test_update_all_failure_from_multi_source_is_propagated(monkeypatch) -> None:
    import config.config
    import database.repositories.factory

    class DummyRepo:
        connection = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        config.config, "load_config", lambda _filename: {"database": {"driver": "snowflake"}}
    )
    monkeypatch.setattr(
        database.repositories.factory, "repository_from_config", lambda _config: DummyRepo()
    )
    monkeypatch.setattr(update_all, "yesterday_warsaw", lambda: date(2026, 11, 18))
    monkeypatch.setattr(update_all, "fetch_inventory", lambda _con, dataset: [dataset])
    monkeypatch.setattr(
        update_all,
        "build_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            jobs=[job("GPW")], skipped=[], target=date(2026, 11, 17)
        ),
    )
    monkeypatch.setattr(update_all.subprocess, "call", lambda _cmd: 1)
    assert update_all.main(["--datasets", "GPW", "--through", "2026-11-17", "--write"]) == 1
