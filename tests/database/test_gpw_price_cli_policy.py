"""Offline CLI regression checks for one-source default and degraded provider warnings."""

from types import ModuleType, SimpleNamespace
import sys

from run.ingest_gpw_prices import main


class FakeRepo:
    connection = object()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _mock_dependencies(monkeypatch, *, run_result):
    config_pkg = ModuleType("config")
    config_pkg.__path__ = []
    config_mod = ModuleType("config.config")
    config_mod.load_config = lambda _path: {"database": {"driver": "snowflake"}}
    factory_mod = ModuleType("database.repositories.factory")
    factory_mod.repository_from_config = lambda _config: FakeRepo()

    import ingestion.gpw_multi_source_job as job
    import ingestion.gpw_provider_feeds as feeds

    seen = {}

    def spy_run_job(_connection, **kwargs):
        seen.update(kwargs)
        return run_result

    monkeypatch.setattr(job, "run_job", spy_run_job)
    monkeypatch.setattr(feeds, "default_feeds", lambda: {})
    monkeypatch.setitem(sys.modules, "config", config_pkg)
    monkeypatch.setitem(sys.modules, "config.config", config_mod)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory_mod)
    return seen


def _result(*, days=1, unresolved=0, errors=("STOOQ authentication required",)):
    return SimpleNamespace(
        symbols_processed=1,
        provider_requests=2,
        days_seen=days,
        candidates_inserted=2,
        promoted=1 if days else 0,
        unresolved=unresolved,
        preserved_conflicts=0,
        provider_errors=errors,
    )


def test_cli_defaults_to_single_valid_source_and_tolerates_failed_secondary(monkeypatch):
    seen = _mock_dependencies(monkeypatch, run_result=_result())
    outcome = main(["--symbols", "PZU.WA", "--start", "2026-11-16", "--end", "2026-11-17"])
    assert outcome == 0
    assert seen["minimum_sources"] == 1
    assert seen["write"] is False


def test_cli_allows_explicit_strict_consensus(monkeypatch):
    seen = _mock_dependencies(monkeypatch, run_result=_result(errors=()))
    outcome = main(
        [
            "--symbols",
            "PZU.WA",
            "--start",
            "2026-11-16",
            "--end",
            "2026-11-17",
            "--minimum-sources",
            "2",
        ]
    )
    assert outcome == 0
    assert seen["minimum_sources"] == 2


def test_cli_must_fail_when_no_sessions_were_delivered(monkeypatch):
    _mock_dependencies(monkeypatch, run_result=_result(days=0))
    outcome = main(["--symbols", "PZU.WA", "--start", "2026-11-16", "--end", "2026-11-17"])
    assert outcome == 1
