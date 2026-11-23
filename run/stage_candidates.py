"""Fetch and persist candidate observations ONLY; never update authoritative prices.

Examples:
    uv run --extra snowflake python -m run.stage_candidates --dataset FX --symbols EURPLN=X --start 2026-11-16 --end 2026-11-18 --write
    uv run --extra snowflake python -m run.stage_candidates --dataset WIG20 --start 2026-11-16 --end 2026-11-18 --write
Run run.promote_candidates separately to publish already-committed candidates.
"""

from __future__ import annotations
import argparse
from datetime import date

DATASETS = (
    "GPW",
    "US",
    "PSE",
    "XETRA",
    "PARIS",
    "LSE",
    "BSE",
    "FX",
    "RATES",
    "WIG20",
    "DERIVATIVES",
    "FW20",
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, choices=DATASETS)
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument(
        "--write", action="store_true", help="Write candidate observations, NOT final prices"
    )
    a = p.parse_args(argv)
    if a.start > a.end:
        p.error("start after end")
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ingestion.candidate_stage_job import run_stage

    cfg = load_config("database.yaml")
    backend = cfg.get("database", {}).get("driver", "").lower()
    if backend not in ("snowflake", "duckdb"):
        p.error("Select a supported database backend")
    with repository_from_config(cfg) as repo:
        report = run_stage(
            repo.connection,
            dataset=a.dataset,
            symbols=a.symbols,
            start=a.start,
            end=a.end,
            backend=backend,
            write=a.write,
        )
    print(
        f"{'STAGED' if a.write else 'DRY_RUN'} {a.dataset}: "
        f"instruments={report.instruments} observed={report.observations} "
        f"new_candidates={report.newly_stored} source_errors={len(report.errors)}"
    )
    for error in report.errors:
        print("WARNING", error)
    # Other valid providers can make one failed provider harmless. Empty result
    # signals no evidence for this job, not an accepted zero-price candle.
    if report.instruments == 0 or (
        report.observations == 0 and a.dataset not in ("DERIVATIVES", "FW20")
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
