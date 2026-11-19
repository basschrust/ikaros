"""Stage all daily observations as candidates, then promote from committed rows.

Examples:
  uv run --extra snowflake python -m run.update_all --datasets WIG20 --write
  uv run --extra snowflake python -m run.update_all --datasets WIG20 --repair-only --write
  uv run --extra snowflake python -m run.update_all --write

Every daily dataset goes through the candidate store. This updater does not
invoke legacy direct-to-final writers; source fetch and promotion are separate.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date

from ikaros_app.update import (
    DATASETS,
    Job,
    build_plan,
    fetch_inventory,
    yesterday_warsaw,
)

LOG = logging.getLogger("ikaros.update")


def _candidate_dataset(dataset: str) -> str:
    """Normalize planner-specific FX labels into one candidate domain."""
    return (
        "FX"
        if dataset in ("FX-YAHOO", "FX-NBP")
        else "DERIVATIVES"
        if dataset == "FW20"
        else dataset
    )


def command_for_job(job: Job) -> tuple[str, ...]:
    """Candidate ingestion only: this worker cannot write final prices."""
    return (
        sys.executable,
        "-m",
        "run.stage_candidates",
        "--dataset",
        _candidate_dataset(job.dataset),
        "--symbols",
        *job.symbols,
        "--start",
        job.start.isoformat(),
        "--end",
        job.end.isoformat(),
    )


def promotion_command_for_job(job: Job) -> tuple[str, ...]:
    """Separate subprocess which only reads stored candidates and promotes."""
    return (
        sys.executable,
        "-m",
        "run.promote_candidates",
        "--dataset",
        _candidate_dataset(job.dataset),
        "--symbols",
        *job.symbols,
        "--start",
        job.start.isoformat(),
        "--end",
        job.end.isoformat(),
    )


def _dedupe_jobs(jobs: list[Job]) -> list[Job]:
    """Avoid executing identical date/symbol jobs more than once."""
    result: list[Job] = []
    seen: set[tuple[str, tuple[str, ...], date, date]] = set()
    for job in jobs:
        identity = (job.dataset, job.symbols, job.start, job.end)
        if identity not in seen:
            result.append(job)
            seen.add(identity)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", choices=DATASETS, nargs="+", default=list(DATASETS))
    parser.add_argument("--write", action="store_true", help="Execute workers (default plan-only)")
    parser.add_argument("--through", type=date.fromisoformat, help="Completed target date")
    parser.add_argument("--overlap-days", type=int, default=2)
    parser.add_argument("--max-gap-days", type=int, default=90)
    parser.add_argument("--bootstrap-start", type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument(
        "--repair-lookback-sessions",
        type=int,
        default=20,
        help="Scan last N completed GPW sessions for earlier missing observations",
    )
    parser.add_argument(
        "--no-gap-repair",
        action="store_true",
        help="Ordinary latest-date catch-up only",
    )
    parser.add_argument(
        "--repair-only",
        action="store_true",
        help="Only repair earlier missing sessions; skip latest-date catch-up",
    )
    parser.add_argument(
        "--recheck-derivatives",
        action="store_true",
        help="Replay futures/options history without inventing no-trade bars",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = args.through or yesterday_warsaw()
    if target > yesterday_warsaw():
        parser.error("--through must be no later than yesterday in Warsaw")
    if args.overlap_days < 0 or args.max_gap_days < 2 or args.batch_size < 1:
        parser.error("Invalid overlap-days, max-gap-days or batch-size")
    if not 1 <= args.repair_lookback_sessions <= 100:
        parser.error("--repair-lookback-sessions must be 1..100")
    if args.repair_only and args.no_gap_repair:
        parser.error("--repair-only and --no-gap-repair are mutually exclusive")
    if args.recheck_derivatives and args.no_gap_repair:
        parser.error("--recheck-derivatives conflicts with --no-gap-repair")
    if args.repair_only and not (
        {"GPW", "WIG20"}.intersection(args.datasets) or args.recheck_derivatives
    ):
        parser.error(
            "--repair-only supports GPW/WIG20, or --recheck-derivatives "
            "for registered futures/options"
        )
    if args.recheck_derivatives and not {"DERIVATIVES", "FW20"}.intersection(args.datasets):
        parser.error("--recheck-derivatives requires DERIVATIVES or FW20 in --datasets")

    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ikaros_app.gap_repair import gap_repair_jobs

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake in config/database.yaml")
    selected = tuple(dict.fromkeys(args.datasets))
    with repository_from_config(config) as repo:
        inventories = {dataset: fetch_inventory(repo.connection, dataset) for dataset in selected}
        plan = (
            build_plan(
                inventories,
                datasets=selected,
                target=target,
                overlap_days=args.overlap_days,
                max_gap_days=args.max_gap_days,
                bootstrap_start=args.bootstrap_start,
                batch_size=args.batch_size,
            )
            if not args.repair_only
            else None
        )
        repairs: list[Job] = []
        if not args.no_gap_repair:
            for dataset in ("WIG20", "GPW"):
                if dataset not in inventories:
                    continue
                missing_jobs, missing = gap_repair_jobs(
                    repo.connection,
                    dataset=dataset,
                    symbols=tuple(item.symbol for item in inventories[dataset]),
                    through=target,
                    lookback_sessions=args.repair_lookback_sessions,
                    batch_size=args.batch_size,
                )
                repairs.extend(missing_jobs)
                LOG.info(
                    "GAPS %s symbols=%d sessions=%d jobs=%d",
                    dataset,
                    len(missing),
                    sum(len(days) for days in missing.values()),
                    len(missing_jobs),
                )
                for symbol, dates in sorted(missing.items()):
                    LOG.info(
                        "MISSING %s %s: %s",
                        dataset,
                        symbol,
                        ", ".join(map(str, dates)),
                    )
        if args.recheck_derivatives:
            from ikaros_app.update import worker
            from run.ingest_gpw_prices import last_gpw_sessions

            first = last_gpw_sessions(target, args.repair_lookback_sessions)[0]
            kind = "DERIVATIVES" if "DERIVATIVES" in inventories else "FW20"
            # No inference: sparse futures/options may legitimately not trade.
            eligible = sorted(
                {
                    row.symbol
                    for row in inventories[kind]
                    if row.expiry is not None and row.expiry >= first
                }
            )
            for offset in range(0, len(eligible), args.batch_size):
                batch = tuple(eligible[offset : offset + args.batch_size])
                repairs.append(
                    Job(
                        kind,
                        batch,
                        first,
                        target,
                        worker("run.ingest_market_prices", "--dataset", "DERIVATIVE"),
                    )
                )
            LOG.info("RECHECK %s contracts=%d from=%s", kind, len(eligible), first)

    # Ordinary catch-up can already encompass some repair dates.
    normal = plan.jobs if plan else []
    pending_repairs = [
        repair
        for repair in repairs
        if not any(
            regular.dataset == repair.dataset
            and set(repair.symbols).issubset(regular.symbols)
            and regular.start <= repair.start
            and regular.end >= repair.end
            for regular in normal
        )
    ]
    jobs = _dedupe_jobs(normal + pending_repairs)
    LOG.info(
        "Completed target=%s; jobs=%d repair_jobs=%d write=%s",
        target,
        len(jobs),
        len(pending_repairs),
        args.write,
    )
    if plan is not None:
        for line in plan.skipped:
            LOG.warning("SKIP %s", line)
    failures = 0
    staged_jobs: list[Job] = []
    # Stage *all* source observations first. Independent reconciler starts only
    # after staging has completed; no stage worker can publish final prices.
    for number, job in enumerate(jobs, 1):
        cmd = list(command_for_job(job)) + (["--write"] if args.write else [])
        LOG.info(
            "STAGE [%d/%d] %s %s..%s symbols=%s: %s",
            number,
            len(jobs),
            job.dataset,
            job.start,
            job.end,
            ",".join(job.symbols),
            " ".join(cmd),
        )
        if args.write:
            if subprocess.call(cmd):
                failures += 1
                LOG.error("STAGE FAILED job %d; promotion will not run for this window", number)
            else:
                staged_jobs.append(job)
    if args.write:
        for number, job in enumerate(staged_jobs, 1):
            cmd = list(promotion_command_for_job(job)) + ["--write"]
            LOG.info(
                "PROMOTE [%d/%d] %s %s..%s symbols=%s: %s",
                number,
                len(staged_jobs),
                job.dataset,
                job.start,
                job.end,
                ",".join(job.symbols),
                " ".join(cmd),
            )
            if subprocess.call(cmd):
                failures += 1
                LOG.error("PROMOTION FAILED job %d", number)
    if args.write and "WIG20" in selected and not args.no_gap_repair:
        # Confirm the hole was actually filled, not just the worker's exit code.
        from ikaros_app.gap_repair import missing_sessions

        with repository_from_config(config) as repo:
            remaining = missing_sessions(
                repo.connection,
                dataset="WIG20",
                symbols=["WIG20"],
                through=target,
                lookback_sessions=args.repair_lookback_sessions,
                batch_size=args.batch_size,
                include_trailing=not args.repair_only,
            )
        if remaining:
            failures += 1
            LOG.error("WIG20 session gaps remain after update: %s", remaining)
    LOG.info(
        "Finished jobs=%d failed=%d dry_run=%s",
        len(jobs),
        failures,
        not args.write,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
