"""Backfill every registered instrument to local DuckDB, candidate-first.

Quick start (after run.setup_local_duckdb --write):
    uv run python -m run.backfill_local --write

Defaults: 2025-01-01 through yesterday in Europe/Warsaw, 60-day windows,
30 symbols per worker. Read-only plan without --write. Never opens Snowflake.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from database.local_backfill_catalog import (
    BACKFILL_DATASETS,
    RegisteredInstrument,
)

LOG = logging.getLogger("ikaros.local_backfill")
DEFAULT_START = date(2025, 1, 1)
DEFAULT_WINDOW_DAYS = 60
DEFAULT_BATCH_SIZE = 30


@dataclass(frozen=True)
class BackfillJob:
    dataset: str
    start: date
    end: date
    symbols: tuple[str, ...]


def yesterday_warsaw() -> date:
    return datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1)


def date_windows(*, start: date, end: date, days: int) -> list[tuple[date, date]]:
    """Non-overlapping, inclusive windows for source-rate-limit protection."""
    if days < 1 or start > end:
        raise ValueError("Require positive days and start <= end")
    windows: list[tuple[date, date]] = []
    current = start
    while current <= end:
        last = min(current + timedelta(days=days - 1), end)
        windows.append((current, last))
        current = last + timedelta(days=1)
    return windows


def build_jobs(
    *,
    inventories: Mapping[str, Sequence[RegisteredInstrument]],
    datasets: Sequence[str],
    start: date,
    end: date,
    window_days: int,
    batch_size: int,
) -> list[BackfillJob]:
    """Build deterministic jobs, including previously expired contracts."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    windows = date_windows(start=start, end=end, days=window_days)
    jobs: list[BackfillJob] = []
    for dataset in dict.fromkeys(datasets):
        if dataset not in BACKFILL_DATASETS:
            raise ValueError(f"Unsupported dataset {dataset}")
        for begin, finish in windows:
            symbols = sorted(
                {
                    item.symbol
                    for item in inventories.get(dataset, ())
                    if (item.valid_from is None or item.valid_from <= finish)
                    and (item.expiry is None or item.expiry >= begin)
                }
            )
            for index in range(0, len(symbols), batch_size):
                jobs.append(
                    BackfillJob(
                        dataset,
                        begin,
                        finish,
                        tuple(symbols[index : index + batch_size]),
                    )
                )
    return jobs


def worker_command(*, phase: str, job: BackfillJob) -> list[str]:
    if phase not in ("stage", "promote"):
        raise ValueError(f"Unknown candidate phase {phase}")
    return [
        sys.executable,
        "-m",
        f"run.{phase}_candidates",
        "--dataset",
        job.dataset,
        "--symbols",
        *job.symbols,
        "--start",
        job.start.isoformat(),
        "--end",
        job.end.isoformat(),
        "--write",
    ]


def execute_jobs(
    *,
    jobs: Sequence[BackfillJob],
    project_root: Path,
    write: bool,
    fail_fast: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """A failed stage never promotes that window; other jobs may continue.

    Nonzero return means one or more windows were not fully processed.
    Rerunning is safe because candidate writes are content-hash-idempotent.
    """
    LOG.info("Historical backfill: jobs=%d write=%s", len(jobs), write)
    if not write:
        for dataset in dict.fromkeys(job.dataset for job in jobs):
            subset = [job for job in jobs if job.dataset == dataset]
            LOG.info(
                "PLAN %s jobs=%d symbols=%d windows=%d",
                dataset,
                len(subset),
                len({symbol for job in subset for symbol in job.symbols}),
                len({(job.start, job.end) for job in subset}),
            )
        LOG.info("DRY RUN: rerun with --write to stage and promote")
        return 0

    environment = os.environ.copy()
    # A child process MUST select local DuckDB, even if YAML defaults change.
    environment["IKAROS_DB_DRIVER"] = "duckdb"
    failures: list[tuple[str, BackfillJob, int]] = []
    for number, job in enumerate(jobs, start=1):
        LOG.info(
            "[%d/%d] %s %s..%s instruments=%d",
            number,
            len(jobs),
            job.dataset,
            job.start,
            job.end,
            len(job.symbols),
        )
        for phase in ("stage", "promote"):
            command = worker_command(phase=phase, job=job)
            try:
                result = runner(
                    command,
                    check=False,
                    cwd=project_root,
                    env=environment,
                )
                status = result.returncode
            except OSError as exc:
                LOG.error("Unable to start %s for %s: %s", phase, job.dataset, exc)
                status = 1
            if status:
                failures.append((phase, job, status))
                LOG.error(
                    "FAILED %s %s %s..%s instruments=%s exit=%s",
                    phase.upper(),
                    job.dataset,
                    job.start,
                    job.end,
                    ",".join(job.symbols),
                    status,
                )
                if fail_fast:
                    LOG.error("Stopped on first failure; rerun after fixing the issue")
                    return 1
                # Never promote after a failed stage.
                break
    if failures:
        LOG.error("Incomplete historical backfill: %d failed jobs", len(failures))
        for phase, job, status in failures:
            LOG.error(
                "RETRY phase=%s dataset=%s start=%s end=%s symbols=%s exit=%d",
                phase,
                job.dataset,
                job.start,
                job.end,
                ",".join(job.symbols),
                status,
            )
        return 1
    LOG.info(
        "Historical backfill finished: %d successful jobs; "
        "inspect source_errors and rejected candidates separately",
        len(jobs),
    )
    return 0


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=BACKFILL_DATASETS,
        default=list(BACKFILL_DATASETS),
    )
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--write", action="store_true", help="Execute; default is a dry-run plan")
    parser.add_argument("--fail-fast", action="store_true", help="Stop at the first failed job")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    through = args.through or yesterday_warsaw()
    if args.start > through:
        parser.error("--start must not follow --through")
    if through > yesterday_warsaw():
        parser.error("--through cannot be later than yesterday in Europe/Warsaw")
    if args.window_days < 1 or args.window_days > 90:
        parser.error("--window-days must be between 1 and 90")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    from config.config import PROJECT_ROOT, load_config
    from database.local_backfill_catalog import registered_instruments
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "duckdb":
        parser.error("Local historical backfill requires IKAROS_DB_DRIVER=duckdb")
    selected = tuple(dict.fromkeys(args.datasets))
    with repository_from_config(config) as repo:
        inventories = {
            dataset: registered_instruments(connection=repo.connection, dataset=dataset)
            for dataset in selected
        }
    for dataset, items in inventories.items():
        if not items:
            LOG.warning(
                "SKIP %s: no registered candidate-mapped instruments in local DuckDB. "
                "Run run.setup_local_duckdb --write "
                "(provide reviewed CSVs where applicable)",
                dataset,
            )
        else:
            LOG.info("REGISTERED %-11s %d instruments", dataset, len(items))
    jobs = build_jobs(
        inventories=inventories,
        datasets=selected,
        start=args.start,
        end=through,
        window_days=args.window_days,
        batch_size=args.batch_size,
    )
    if not jobs:
        LOG.error("No eligible instruments; refusing an empty backfill")
        return 1
    LOG.info("Local DuckDB backfill %s..%s; %d jobs", args.start, through, len(jobs))
    return execute_jobs(
        jobs=jobs,
        project_root=PROJECT_ROOT,
        write=args.write,
        fail_fast=args.fail_fast,
    )


if __name__ == "__main__":
    raise SystemExit(main())
