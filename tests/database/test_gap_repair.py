"""SQL-backed regression tests for actual interior session gaps and reruns."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from ikaros_app.gap_repair import gap_repair_jobs, missing_sessions
from run import update_all


class Cursor:
    def __init__(self, inner: sqlite3.Cursor):
        self.inner = inner

    def execute(self, sql: str, params=()) -> "Cursor":
        sql = sql.replace("%s", "?").replace('CAST("DATE" AS DATE)', 'DATE("DATE")')
        self.inner.execute(sql, params)
        return self

    def fetchall(self):
        return self.inner.fetchall()

    def close(self) -> None:
        self.inner.close()


class DB:
    def __init__(self):
        self.inner = sqlite3.connect(":memory:")
        self.inner.executescript("""
            CREATE TABLE INDEX_PRICES (INDEX_SYMBOL TEXT, DATE TEXT);
            CREATE TABLE PRICES (SYMBOL TEXT, DATE TEXT);
            INSERT INTO INDEX_PRICES VALUES
              ('WIG20','2026-11-10'),
              ('WIG20','2026-11-12'),
              ('WIG20','2026-11-13'),
              ('WIG20','2026-11-17'),
              ('WIG20','2026-11-18');
            INSERT INTO PRICES VALUES
              ('PZU.WA','2026-11-10'),
              ('PZU.WA','2026-11-12'),
              ('PZU.WA','2026-11-13'),
              ('PZU.WA','2026-11-17'),
              ('PZU.WA','2026-11-18');
            INSERT INTO PRICES VALUES
              ('ALE.WA','2026-11-10'),
              ('ALE.WA','2026-11-12'),
              ('ALE.WA','2026-11-13'),
              ('ALE.WA','2026-11-16'),
              ('ALE.WA','2026-11-17'),
              ('ALE.WA','2026-11-18');
        """)

    def cursor(self) -> Cursor:
        return Cursor(self.inner.cursor())


def test_index_finds_one_hole_but_not_november_11_holiday():
    db = DB()
    missing = missing_sessions(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    )
    assert missing == {"WIG20": (date(2026, 11, 16),)}
    jobs, _ = gap_repair_jobs(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    )
    assert len(jobs) == 1
    assert jobs[0].start == jobs[0].end == date(2026, 11, 16)
    command = update_all.command_for_job(jobs[0])
    assert command[2:4] == ("run.ingest_market_prices", "--dataset")
    assert command[4] == "INDEX"
    assert command[-4:] == ("--start", "2026-11-16", "--end", "2026-11-16")


def test_repaired_index_gap_not_scheduled_again():
    db = DB()
    db.inner.execute("INSERT INTO INDEX_PRICES VALUES ('WIG20','2026-11-16')")
    jobs, missing = gap_repair_jobs(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    )
    assert jobs == [] and missing == {}


def test_equity_hole_only_for_affected_symbol_and_gap_job_uses_candidates():
    db = DB()
    jobs, missing = gap_repair_jobs(
        db,
        dataset="GPW",
        symbols=["PZU.WA", "ALE.WA", "NOHISTORY.WA"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    )
    assert missing == {"PZU.WA": (date(2026, 11, 16),)}
    assert len(jobs) == 1
    command = update_all.command_for_job(jobs[0])
    assert command[2] == "run.ingest_gpw_prices"
    assert "PZU.WA" in command
    assert "ALE.WA" not in command


def test_initial_listing_not_misdiagnosed_as_historical_hole():
    db = DB()
    db.inner.execute("INSERT INTO PRICES VALUES ('NEW.WA','2026-11-17')")
    db.inner.execute("INSERT INTO PRICES VALUES ('NEW.WA','2026-11-18')")
    assert (
        missing_sessions(
            db,
            dataset="GPW",
            symbols=["NEW.WA"],
            through=date(2026, 11, 18),
            lookback_sessions=7,
        )
        == {}
    )


def test_consecutive_trading_sessions_coalesce_across_weekend():
    db = DB()
    db.inner.execute("DELETE FROM INDEX_PRICES WHERE DATE IN ('2026-11-13','2026-11-17')")
    # 13th, 16th, 17th are consecutive completed GPW sessions.
    jobs, missing = gap_repair_jobs(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    )
    assert missing == {
        "WIG20": (
            date(2026, 11, 13),
            date(2026, 11, 16),
            date(2026, 11, 17),
        )
    }
    assert len(jobs) == 1
    assert jobs[0].start == date(2026, 11, 13)
    assert jobs[0].end == date(2026, 11, 17)


def test_bad_dataset_or_missing_calendar_fails_closed():
    db = DB()
    with pytest.raises(ValueError, match="No GPW session calendar"):
        missing_sessions(db, dataset="FX", symbols=["EURPLN=X"], through=date(2026, 11, 18))
    with pytest.raises(ValueError, match="Official GPW trading calendar"):
        missing_sessions(db, dataset="WIG20", symbols=["WIG20"], through=date(2028, 1, 3))


def test_future_contracts_are_routed_through_candidates_not_legacy():
    from ikaros_app.update import Job

    entry = Job(
        "FW20",
        ("FW20Z2620",),
        date(2026, 11, 16),
        date(2026, 11, 18),
        ("python", "-m", "run.refresh_fw20_prices"),
    )
    argv = update_all.command_for_job(entry)
    assert argv[2:5] == ("run.ingest_market_prices", "--dataset", "DERIVATIVE")


def test_duplicate_jobs_not_executed_twice():
    from ikaros_app.update import Job

    j = Job("WIG20", ("WIG20",), date(2026, 11, 16), date(2026, 11, 16), ("x",))
    assert update_all._dedupe_jobs([j, j]) == [j]


def _patch_update_dependencies(monkeypatch, db):
    """Keep the real gap scanner, but replace Snowflake/config/process boundaries."""
    import sys
    from types import ModuleType, SimpleNamespace

    cfg_pkg = ModuleType("config")
    cfg_pkg.__path__ = []
    cfg = ModuleType("config.config")
    cfg.load_config = lambda _name: {"database": {"driver": "snowflake"}}
    factory = ModuleType("database.repositories.factory")

    class Repo:
        connection = db

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    factory.repository_from_config = lambda _config: Repo()
    monkeypatch.setitem(sys.modules, "config", cfg_pkg)
    monkeypatch.setitem(sys.modules, "config.config", cfg)
    monkeypatch.setitem(sys.modules, "database.repositories.factory", factory)
    monkeypatch.setattr(update_all, "yesterday_warsaw", lambda: date(2026, 11, 19))
    monkeypatch.setattr(
        update_all,
        "fetch_inventory",
        lambda _conn, dataset: [SimpleNamespace(symbol="WIG20")],
    )


def test_repair_only_executes_gap_job_and_verifies_readback(monkeypatch):
    db = DB()
    _patch_update_dependencies(monkeypatch, db)
    calls = []

    def execute(cmd):
        calls.append(cmd)
        assert cmd[2] == "run.ingest_market_prices"
        assert cmd[-1] == "--write"
        db.inner.execute("INSERT INTO INDEX_PRICES VALUES ('WIG20','2026-11-16')")
        return 0

    monkeypatch.setattr(update_all.subprocess, "call", execute)
    assert (
        update_all.main(
            [
                "--datasets",
                "WIG20",
                "--repair-only",
                "--write",
                "--through",
                "2026-11-18",
                "--repair-lookback-sessions",
                "7",
            ]
        )
        == 0
    )
    assert len(calls) == 1


def test_worker_success_is_not_enough_if_index_hole_remains(monkeypatch):
    db = DB()
    _patch_update_dependencies(monkeypatch, db)
    monkeypatch.setattr(update_all.subprocess, "call", lambda _command: 0)
    assert (
        update_all.main(
            [
                "--datasets",
                "WIG20",
                "--repair-only",
                "--write",
                "--through",
                "2026-11-18",
                "--repair-lookback-sessions",
                "7",
            ]
        )
        == 1
    )


def test_unrequested_fx_or_rates_never_masquerade_as_gpw_trading_gaps(monkeypatch):
    db = DB()
    _patch_update_dependencies(monkeypatch, db)
    with pytest.raises(SystemExit, match="2"):
        update_all.main(
            [
                "--datasets",
                "FX",
                "--repair-only",
                "--through",
                "2026-11-18",
            ]
        )


def test_explicit_derivative_history_replay_never_creates_synthetic_quotes(monkeypatch):
    from types import SimpleNamespace

    db = DB()
    _patch_update_dependencies(monkeypatch, db)
    monkeypatch.setattr(
        update_all,
        "fetch_inventory",
        lambda _conn, dataset: [
            SimpleNamespace(symbol="FW20Z2620", expiry=date(2026, 12, 18)),
            SimpleNamespace(symbol="FW20U2620", expiry=date(2026, 9, 18)),
        ],
    )
    commands = []
    monkeypatch.setattr(
        update_all.subprocess,
        "call",
        lambda command: commands.append(command) or 0,
    )
    assert (
        update_all.main(
            [
                "--datasets",
                "DERIVATIVES",
                "--repair-only",
                "--recheck-derivatives",
                "--write",
                "--through",
                "2026-11-18",
                "--repair-lookback-sessions",
                "5",
            ]
        )
        == 0
    )
    assert len(commands) == 1
    assert commands[0][2:5] == ["run.ingest_market_prices", "--dataset", "DERIVATIVE"]
    assert "FW20Z2620" in commands[0]
    assert "FW20U2620" not in commands[0]
    assert commands[0][-5:] == ["--start", "2026-11-12", "--end", "2026-11-18", "--write"]


def test_final_index_coverage_check_includes_stale_last_session():
    db = DB()
    db.inner.execute("DELETE FROM INDEX_PRICES WHERE DATE='2026-11-18'")
    # Scan only genuine interior holes before latest price.
    assert missing_sessions(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
    ) == {"WIG20": (date(2026, 11, 16),)}
    # After normal catch-up, also require yesterday's completed WIG20 bar.
    assert missing_sessions(
        db,
        dataset="WIG20",
        symbols=["WIG20"],
        through=date(2026, 11, 18),
        lookback_sessions=7,
        include_trailing=True,
    ) == {"WIG20": (date(2026, 11, 16), date(2026, 11, 18))}
