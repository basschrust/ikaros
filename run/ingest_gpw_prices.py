"""Multi-provider GPW daily OHLCV ingestion with candidates and safe promotion.

Examples (PowerShell):
  uv run --extra snowflake python -m run.ingest_gpw_prices --symbols PZU.WA --start 2026-10-14 --end 2026-11-17
  uv run --extra snowflake python -m run.ingest_gpw_prices --symbols PZU.WA --start 2026-10-14 --end 2026-11-17 --write
  uv run --extra snowflake python -m run.ingest_gpw_prices --all-mapped --write
  uv run --extra snowflake python -m run.ingest_gpw_prices --wig20 --write

Dry-run by default. Assumes the old direct GPW sink is stopped.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


# Exchange closures from GPW Board resolutions 753/2025 (2026) and
# 754/2025 (2027). The dataset intentionally fails closed for unknown years
# instead of silently treating an exchange holiday as a working session.
# Data source (2026): https://www.pekao.com.pl/biuro-maklerskie/inne-regulacje/dni-bez-sesji-na-GPW-2026.html
GPW_NON_TRADING_DAYS: dict[int, frozenset[date]] = {
    2026: frozenset(
        date.fromisoformat(value)
        for value in (
            "2026-01-01",
            "2026-01-06",
            "2026-04-03",
            "2026-04-06",
            "2026-05-01",
            "2026-06-04",
            "2026-11-11",
            "2026-12-24",
            "2026-12-25",
            "2026-12-31",
        )
    ),
    2027: frozenset(
        date.fromisoformat(value)
        for value in (
            "2027-01-01",
            "2027-01-06",
            "2027-03-26",
            "2027-03-29",
            "2027-05-03",
            "2027-05-27",
            "2027-11-01",
            "2027-11-11",
            "2027-12-24",
            "2027-12-31",
        )
    ),
}


def _warsaw_yesterday() -> date:
    return datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1)


def last_gpw_sessions(through: date, count: int) -> tuple[date, ...]:
    """Select completed weekday trading sessions, excluding official GPW closures.

    A missing exchange calendar raises rather than silently using weekdays.
    Keep published dates updated as GPW announces future years. For a longer
    term solution, this helper can read the project's TRADING_DAYS reference.
    """
    if not 1 <= count <= 100:
        raise ValueError("last-sessions must be between 1 and 100")
    result: list[date] = []
    cursor = through
    while len(result) < count:
        closures = GPW_NON_TRADING_DAYS.get(cursor.year)
        if closures is None:
            raise ValueError(
                f"Official GPW trading calendar unavailable for {cursor.year}; "
                "update GPW_NON_TRADING_DAYS before running"
            )
        if cursor.weekday() < 5 and cursor not in closures:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(reversed(result))


def wig20_symbols(path: Path | None = None) -> list[str]:
    """Load the reviewed 20-stock universe; never infer membership from all GPW."""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "data" / "provider_symbols_gpw_wig20.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(
        row.get("venue") != "GPW" or not row.get("canonical_symbol", "").endswith(".WA")
        for row in rows
    ):
        raise ValueError(f"Invalid WIG20 reference CSV: {path}")
    symbols = sorted({row["canonical_symbol"] for row in rows})
    if len(symbols) != 20:
        raise ValueError(
            f"WIG20 reference CSV must contain exactly 20 symbols; found {len(symbols)}"
        )
    return symbols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbols", nargs="+", help="Canonical SECURITIES symbols (e.g. PZU.WA)")
    group.add_argument(
        "--all-mapped", action="store_true", help="All GPW assets with at least one active mapping"
    )
    group.add_argument(
        "--wig20",
        action="store_true",
        help="20 reviewed WIG20 stocks; last 5 completed sessions by default",
    )
    yesterday = _warsaw_yesterday()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument(
        "--last-sessions", type=int, help="Completed GPW sessions (default 5 with --wig20)"
    )
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
    if args.last_sessions is not None and not args.wig20:
        parser.error("--last-sessions is currently supported with --wig20 only")
    if args.last_sessions is not None and (args.start or args.end):
        parser.error("Choose --last-sessions OR --start/--end, not both")
    if (args.start is None) != (args.end is None):
        parser.error("Specify --start and --end together")
    try:
        session_count = args.last_sessions if args.last_sessions is not None else 5
        if args.wig20 and args.start is None:
            sessions = last_gpw_sessions(yesterday, session_count)
            start, end = sessions[0], sessions[-1]
        else:
            start = args.start or yesterday
            end = args.end or yesterday
        symbols = wig20_symbols() if args.wig20 else (None if args.all_mapped else args.symbols)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if start > end or end > yesterday:
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
    if args.wig20:
        logging.info(
            "WIG20: %d stocks, dates=%s..%s, rolling_sessions=%s",
            len(symbols),
            start,
            end,
            session_count if args.start is None else "explicit dates",
        )
    with repository_from_config(config) as repo:
        result = run_job(
            repo.connection,
            feeds=default_feeds(),
            symbols=symbols,
            start=start,
            end=end,
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
