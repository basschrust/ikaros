"""Read-only GPW Snowflake coverage monitor.

Example:
    uv run --extra snowflake python -m run.gpw_freshness --as-of 2026-10-26 --csv output/gpw_freshness.csv
"""

from __future__ import annotations

import argparse
import logging
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from ingestion.gpw_freshness import (
    expected_last_session,
    freshness_status,
    weekdays_behind,
)

LOGGER = logging.getLogger(__name__)

QUERY = '''
SELECT
    s."SYMBOL" AS SYMBOL,
    s."NAME" AS NAME,
    COUNT(p."DATE") AS ROW_COUNT,
    MIN(TO_DATE(p."DATE")) AS FIRST_DATE,
    MAX(TO_DATE(p."DATE")) AS LAST_DATE,
    COUNT_IF(TO_DATE(p."DATE") BETWEEN TO_DATE(%s) AND TO_DATE(%s)) AS ROWS_IN_PERIOD
FROM "SECURITIES" AS s
LEFT JOIN "PRICES" AS p ON p."SYMBOL" = s."SYMBOL"
WHERE s."VENUE" = 'GPW'
GROUP BY s."SYMBOL", s."NAME"
ORDER BY s."SYMBOL"
'''


def _as_date(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def build_report(rows, *, expected: date, allowed_lag: int) -> pd.DataFrame:
    results = []
    for symbol, name, total, first, last, in_period in rows:
        observed = _as_date(last)
        lag = weekdays_behind(observed, expected)
        results.append({
            "symbol": str(symbol),
            "name": name,
            "total_rows": int(total or 0),
            "rows_in_period": int(in_period or 0),
            "first_date": _as_date(first),
            "last_date": observed,
            "expected_date": expected,
            "weekdays_behind": lag,
            "status": freshness_status(lag, allowed_lag),
        })
    return pd.DataFrame(results, columns=[
        "symbol", "name", "total_rows", "rows_in_period", "first_date", "last_date",
        "expected_date", "weekdays_behind", "status",
    ])


def main() -> None:
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--period-start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--max-lag-sessions", type=int, default=0)
    parser.add_argument("--include-today", action="store_true",
                        help="Require today's daily candle (normally premature during the session)")
    parser.add_argument("--csv", type=Path, help="Optional CSV report path")
    parser.add_argument("--show", type=int, default=20, help="Number of stale/no-data securities to print")
    args = parser.parse_args()
    if args.period_start > args.as_of:
        parser.error("--period-start cannot follow --as-of")
    if args.max_lag_sessions < 0 or args.show < 0:
        parser.error("--max-lag-sessions and --show must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    expected = expected_last_session(args.as_of, include_today=args.include_today)
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("config/database.yaml must use the Snowflake driver")
    with repository_from_config(config) as repo:
        with closing(repo.connection.cursor()) as cur:
            cur.execute(QUERY, (args.period_start.isoformat(), expected.isoformat()))
            rows = cur.fetchall()
    report = build_report(rows, expected=expected, allowed_lag=args.max_lag_sessions)
    if report.empty:
        LOGGER.warning("No GPW securities registered in Snowflake")
        return
    counts = report["status"].value_counts().to_dict()
    LOGGER.info("Expected weekday session: %s (holiday/suspension calendar not yet applied)", expected)
    LOGGER.info("GPW symbols=%d current=%d stale=%d no_data=%d",
                len(report), counts.get("CURRENT", 0), counts.get("STALE", 0),
                counts.get("NO_DATA", 0))
    problems = report.loc[report["status"] != "CURRENT"].sort_values(
        ["status", "weekdays_behind", "symbol"], ascending=[True, False, True],
        na_position="first",
    )
    if args.show and not problems.empty:
        print(problems.head(args.show).to_string(index=False))
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        report.to_csv(args.csv, index=False)
        LOGGER.info("Saved %s", args.csv)


if __name__ == "__main__":
    main()
