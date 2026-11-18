"""Refresh datasets through yesterday, using candidate pipelines for GPW/index/derivatives.

    uv run --extra snowflake python -m run.update_all --datasets WIG20 DERIVATIVES --write

Plan-only by default. The legacy FW20 option still invokes its older worker;
DERIVATIVES is the supported multi-provider route for contract prices.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date

from ikaros_app.update import DATASETS, Job, build_plan, fetch_inventory, yesterday_warsaw

LOG = logging.getLogger("ikaros.update")


def command_for_job(job: Job) -> tuple[str, ...]:
    """Route only migrated datasets; preserve the existing incremental planner."""
    if job.dataset == "GPW":
        return (
            sys.executable,
            "-m",
            "run.ingest_gpw_prices",
            "--symbols",
            *job.symbols,
            "--start",
            job.start.isoformat(),
            "--end",
            job.end.isoformat(),
        )
    if job.dataset in ("WIG20", "DERIVATIVES"):
        return (
            sys.executable,
            "-m",
            "run.ingest_market_prices",
            "--dataset",
            "INDEX" if job.dataset == "WIG20" else "DERIVATIVE",
            "--symbols",
            *job.symbols,
            "--start",
            job.start.isoformat(),
            "--end",
            job.end.isoformat(),
        )
    return job.argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", choices=DATASETS, nargs="+", default=list(DATASETS))
    parser.add_argument("--write", action="store_true", help="Execute workers (default plan-only)")
    parser.add_argument(
        "--through", type=date.fromisoformat, help="Override Warsaw yesterday for a replay"
    )
    parser.add_argument(
        "--overlap-days", type=int, default=2, help="Recheck this many days before latest"
    )
    parser.add_argument("--max-gap-days", type=int, default=90, help="Skip very stale symbols")
    parser.add_argument(
        "--bootstrap-start",
        type=date.fromisoformat,
        help="Opt in to loading instruments with no stored history",
    )
    parser.add_argument("--batch-size", type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = args.through or yesterday_warsaw()
    if target > yesterday_warsaw():
        parser.error("--through must be no later than yesterday in Warsaw")
    if args.overlap_days < 0 or args.max_gap_days < 2 or args.batch_size < 1:
        parser.error("Invalid overlap-days, max-gap-days or batch-size")
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake in config/database.yaml")
    with repository_from_config(config) as repo:
        inventories = {
            dataset: fetch_inventory(repo.connection, dataset)
            for dataset in dict.fromkeys(args.datasets)
        }
    plan = build_plan(
        inventories,
        datasets=tuple(dict.fromkeys(args.datasets)),
        target=target,
        overlap_days=args.overlap_days,
        max_gap_days=args.max_gap_days,
        bootstrap_start=args.bootstrap_start,
        batch_size=args.batch_size,
    )
    LOG.info(
        "Target completed-date=%s; jobs=%d; skipped=%d; write=%s",
        plan.target,
        len(plan.jobs),
        len(plan.skipped),
        args.write,
    )
    for dataset, members in inventories.items():
        LOG.info("Inventory %s instruments=%d", dataset, len(members))
    for line in plan.skipped:
        LOG.warning("SKIP %s", line)
    failures = 0
    for number, job in enumerate(plan.jobs, 1):
        cmd = list(command_for_job(job)) + (["--write"] if args.write else [])
        LOG.info(
            "[%d/%d] %s symbols=%d %s..%s %s",
            number,
            len(plan.jobs),
            job.dataset,
            len(job.symbols),
            job.start,
            job.end,
            " ".join(cmd),
        )
        if args.write:
            code = subprocess.call(cmd)
            if code:
                failures += 1
                LOG.error("FAIL job %d returned %d", number, code)
    LOG.info(
        "Finished jobs=%d failed=%d skipped=%d dry_run=%s",
        len(plan.jobs),
        failures,
        len(plan.skipped),
        not args.write,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
