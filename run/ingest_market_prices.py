"""Ingest WIG20 index or registered GPW futures/options through candidate selection.

    uv run --extra snowflake python -m run.ingest_market_prices --dataset INDEX --write
    uv run --extra snowflake python -m run.ingest_market_prices --dataset DERIVATIVE --write

Defaults: last five *completed* GPW trading sessions. Dry run by default.
Run setup_market_sources --write once before using this command.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("INDEX", "DERIVATIVE"), required=True)
    parser.add_argument("--symbols", nargs="+", help="Optional WIG20 or registered contract IDs")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--last-sessions", type=int, default=5)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    from run.ingest_gpw_prices import _warsaw_yesterday, last_gpw_sessions

    through = _warsaw_yesterday()
    if bool(args.start) != bool(args.end):
        parser.error("Specify both --start and --end, or neither")
    if args.start is None:
        days = last_gpw_sessions(through, args.last_sessions)
        start, end = days[0], days[-1]
    else:
        start, end = args.start, args.end
    if end > through or start > end:
        parser.error("Only completed sessions may be ingested")
    if args.dataset == "INDEX" and args.symbols is not None and args.symbols != ["WIG20"]:
        parser.error("Only reviewed index WIG20 currently supported")
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from database.market_quote_pipeline import run_market_job
    from database.market_metadata import eligible_derivative_contracts
    from ingestion.market_quote_feeds import default_feeds

    settings = load_config("database.yaml")
    backend = settings.get("database", {}).get("driver", "").lower()
    if backend not in ("snowflake", "duckdb"):
        parser.error("Select Snowflake or DuckDB backend")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with repository_from_config(settings) as repo:
        wanted = args.symbols
        if args.dataset == "INDEX":
            wanted = ["WIG20"]
        elif wanted is None:
            wanted = eligible_derivative_contracts(repo.connection, since=start, backend=backend)
        if not wanted:
            print("No registered contracts eligible for this date range")
            return 1
        result = run_market_job(
            repo.connection,
            kind=args.dataset,
            feeds=default_feeds(args.dataset),
            start=start,
            end=end,
            write=args.write,
            backend=backend,
            symbols=wanted,
        )
    print(
        f"{'WRITE' if args.write else 'DRY RUN'} {args.dataset} "
        f"instruments={result.instruments} candidates_new={result.candidates_added} "
        f"promoted={result.promoted} identical={result.identical} "
        f"conflicts={result.conflicts} rejected={result.rejected} "
        f"no_data={result.no_data} sessions_seen={result.sessions_seen} "
        f"selected_days={result.selected_days} "
        f"provider_errors={len(result.source_errors)}"
    )
    for error in result.source_errors:
        print("WARNING:", error)
    # Empty/not-traded option series need review rather than synthetic candles.
    return 1 if not result.instruments or not result.selected_days or result.conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
