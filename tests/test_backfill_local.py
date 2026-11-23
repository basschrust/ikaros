"""Offline tests for a one-command local, candidate-first historical update."""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from database.local_backfill_catalog import RegisteredInstrument, registered_instruments
from run.backfill_local import (
    BackfillJob,
    build_jobs,
    date_windows,
    execute_jobs,
    worker_command,
)


def test_date_windows_are_inclusive_nonoverlapping() -> None:
    assert date_windows(start=date(2025, 1, 1), end=date(2025, 3, 3), days=30) == [
        (date(2025, 1, 1), date(2025, 1, 30)),
        (date(2025, 1, 31), date(2025, 3, 1)),
        (date(2025, 3, 2), date(2025, 3, 3)),
    ]


def test_build_jobs_batches_and_includes_expired_historical_contracts() -> None:
    items = {
        "FX": [RegisteredInstrument("EURPLN=X"), RegisteredInstrument("USDPLN=X")],
        "DERIVATIVES": [
            RegisteredInstrument("FW20H2520", expiry=date(2025, 3, 21)),
            RegisteredInstrument("FW20Z2620", valid_from=date(2026, 1, 1)),
        ],
    }
    jobs = build_jobs(
        inventories=items,
        datasets=["FX", "DERIVATIVES"],
        start=date(2025, 3, 1),
        end=date(2025, 4, 15),
        window_days=30,
        batch_size=1,
    )
    assert [job.symbols for job in jobs if job.dataset == "FX"] == [
        ("EURPLN=X",),
        ("USDPLN=X",),
        ("EURPLN=X",),
        ("USDPLN=X",),
    ]
    assert [(job.start, job.symbols) for job in jobs if job.dataset == "DERIVATIVES"] == [
        (date(2025, 3, 1), ("FW20H2520",))
    ]


def test_candidate_phase_commands_have_explicit_boundaries() -> None:
    job = BackfillJob("FX", date(2025, 1, 1), date(2025, 2, 1), ("EURPLN=X",))
    stage = worker_command(phase="stage", job=job)
    promote = worker_command(phase="promote", job=job)
    assert stage[1:3] == ["-m", "run.stage_candidates"]
    assert promote[1:3] == ["-m", "run.promote_candidates"]
    assert "--write" in stage and "--symbols" in stage
    assert stage[-5:] == ["--start", "2025-01-01", "--end", "2025-02-01", "--write"]


def test_dry_run_does_not_launch_subprocesses(tmp_path: Path) -> None:
    job = BackfillJob("FX", date(2025, 1, 1), date(2025, 1, 2), ("EURPLN=X",))

    def forbidden(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("No worker should be launched")

    assert (
        execute_jobs(
            jobs=[job],
            project_root=tmp_path,
            write=False,
            fail_fast=False,
            runner=forbidden,
        )
        == 0
    )


def test_failed_stage_does_not_promote_and_other_jobs_continue(tmp_path: Path) -> None:
    jobs = [
        BackfillJob("FX", date(2025, 1, 1), date(2025, 1, 2), ("EURPLN=X",)),
        BackfillJob("FX", date(2025, 1, 3), date(2025, 1, 4), ("USDPLN=X",)),
    ]
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        assert kwargs["env"]["IKAROS_DB_DRIVER"] == "duckdb"
        assert kwargs["cwd"] == tmp_path
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    assert (
        execute_jobs(jobs=jobs, project_root=tmp_path, write=True, fail_fast=False, runner=runner)
        == 1
    )
    assert [cmd[2] for cmd in calls] == [
        "run.stage_candidates",
        "run.stage_candidates",
        "run.promote_candidates",
    ]


def test_fail_fast_and_success(tmp_path: Path) -> None:
    job = BackfillJob("FX", date(2025, 1, 1), date(2025, 1, 2), ("EURPLN=X",))
    calls: list[str] = []

    def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command[2])
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    assert (
        execute_jobs(jobs=[job], project_root=tmp_path, write=True, fail_fast=True, runner=runner)
        == 1
    )
    assert calls == ["run.stage_candidates"]

    def succeeds(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0)

    assert (
        execute_jobs(jobs=[job], project_root=tmp_path, write=True, fail_fast=True, runner=succeeds)
        == 0
    )


def test_invalid_window_and_dataset() -> None:
    with pytest.raises(ValueError):
        date_windows(start=date(2025, 2, 1), end=date(2025, 1, 1), days=60)
    with pytest.raises(ValueError):
        build_jobs(
            inventories={},
            datasets=["UNKNOWN"],
            start=date(2025, 1, 1),
            end=date(2025, 1, 2),
            window_days=60,
            batch_size=30,
        )


def test_local_catalog_reads_only_registered_mapped_instruments() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE SECURITIES (ASSET_ID INTEGER, SYMBOL TEXT, VENUE TEXT, IS_ACTIVE BOOLEAN);
        CREATE TABLE PROVIDER_SYMBOLS (ASSET_ID INTEGER, PROVIDER_ID INTEGER,
            VALID_FROM TEXT, IS_ACTIVE BOOLEAN);
        CREATE TABLE PROVIDERS (PROVIDER_ID INTEGER, IS_ACTIVE BOOLEAN);
        CREATE TABLE CURRENCY_PAIRS (SYMBOL TEXT, IS_ACTIVE BOOLEAN);
        CREATE TABLE DERIVATIVE_CONTRACTS (CONTRACT_ID TEXT, EXPIRY_DATE TEXT, INSTRUMENT_TYPE TEXT);
        CREATE TABLE DERIVATIVE_PROVIDER_SYMBOLS
          (CONTRACT_ID TEXT, VALID_FROM TEXT, IS_ACTIVE BOOLEAN);
        CREATE TABLE INDEX_PROVIDER_SYMBOLS (INDEX_SYMBOL TEXT, VALID_FROM TEXT, IS_ACTIVE BOOLEAN);
        CREATE TABLE RATE_SERIES (SERIES_ID TEXT, IS_ACTIVE BOOLEAN);
        INSERT INTO SECURITIES VALUES (1,'ALE.WA','GPW',1),(2,'PZU.WA','GPW',1),(3,'AAPL','US',1);
        INSERT INTO PROVIDERS VALUES (10,1);
        INSERT INTO PROVIDER_SYMBOLS VALUES (1,10,'2024-01-01',1),(3,10,'2025-01-01',1);
        INSERT INTO CURRENCY_PAIRS VALUES ('EURPLN=X',1),('FAKE=X',0);
        INSERT INTO DERIVATIVE_CONTRACTS VALUES ('FW20H2520','2025-03-21','FUTURE');
        INSERT INTO DERIVATIVE_PROVIDER_SYMBOLS VALUES ('FW20H2520','2024-12-20',1);
        INSERT INTO INDEX_PROVIDER_SYMBOLS VALUES ('WIG20','2020-01-01',1);
        INSERT INTO RATE_SERIES VALUES ('ESTR',1);
    """)

    class Conn:
        def cursor(self) -> sqlite3.Cursor:
            return db.cursor()

    conn = Conn()
    assert [x.symbol for x in registered_instruments(connection=conn, dataset="GPW")] == ["ALE.WA"]
    assert [x.symbol for x in registered_instruments(connection=conn, dataset="US")] == ["AAPL"]
    assert [x.symbol for x in registered_instruments(connection=conn, dataset="FX")] == ["EURPLN=X"]
    assert registered_instruments(connection=conn, dataset="DERIVATIVES")[0].expiry == date(
        2025, 3, 21
    )
    assert registered_instruments(connection=conn, dataset="WIG20")[0].symbol == "WIG20"
    assert registered_instruments(connection=conn, dataset="RATES")[0].symbol == "ESTR"
