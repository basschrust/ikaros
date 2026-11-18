"""Multi-provider GPW daily OHLCV ingestion with candidates and safe promotion.

Examples (PowerShell):
  uv run --extra snowflake python -m run.ingest_gpw_prices --symbols PZU.WA --start 2026-10-14 --end 2026-11-17
  uv run --extra snowflake python -m run.ingest_gpw_prices --symbols PZU.WA --start 2026-10-14 --end 2026-11-17 --write
  uv run --extra snowflake python -m run.ingest_gpw_prices --all-mapped --write

Dry-run by default. Assumes the old direct GPW sink is stopped.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols", nargs="+", help="Canonical SECURITIES symbols (e.g. PZU.WA)")
    group.add_argument(
        "--all-mapped", action="store_true", help="All GPW assets with at least one active mapping"
    )
    yesterday = datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1)
    parser.add_argument("--start", type=date.fromisoformat, default=yesterday)
    parser.add_argument("--end", type=date.fromisoformat, default=yesterday)
    parser.add_argument(
        "--minimum-sources",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="1=best valid source (default), 2/3=independent consensus",
    )
    parser.add_argument("--request-delay", type=float, default=0.4)
    parser.add_argument(
        "--write", action="store_true", help="Persist candidates and promote selected valid candles"
    )
    parser.add_argument(
        "--allow-one-volume-source",
        action="store_true",
        help=(
            "Explicit opt-in: accept full OHLCV from one valid provider when its "
            "OHLC matches another provider's rejected zero-volume observation; "
            "volume then remains uncorroborated"
        ),
    )
    args = parser.parse_args(argv)
    if args.start > args.end or args.end > yesterday:
        parser.error("Use completed sessions only: start <= end <= yesterday (Warsaw)")
    if args.request_delay < 0:
        parser.error("--request-delay must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ingestion.gpw_multi_source_job import run_job
    from ingestion.gpw_provider_feeds import default_feeds

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("database.driver must be snowflake")
    with repository_from_config(config) as repo:
        result = run_job(
            repo.connection,
            feeds=default_feeds(),
            symbols=None if args.all_mapped else args.symbols,
            start=args.start,
            end=args.end,
            write=args.write,
            request_delay=args.request_delay,
            minimum_sources=args.minimum_sources,
            allow_one_volume_source=args.allow_one_volume_source,
        )
    print(
        f"{'WRITE' if args.write else 'DRY RUN'}: "
        f"symbols={result.symbols_processed} provider_requests={result.provider_requests} "
        f"days={result.days_seen} candidates_new={result.candidates_inserted} "
        f"promoted={result.promoted} unresolved={result.unresolved} "
        f"preserved_existing_conflicts={result.preserved_conflicts} "
        f"source_errors={len(result.provider_errors)}"
    )
    for failure in result.provider_errors:
        print("WARNING:", failure)
    # A failed provider is a warning when another validated source delivered a
    # complete, accepted candle. Fully missing/invalid days remain failures.
    if (
        result.symbols_processed == 0
        or result.days_seen == 0
        or result.unresolved
        or result.preserved_conflicts
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
