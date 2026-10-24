"""Seed GPW security reference data into existing Snowflake SECURITIES.

Dry-run by default. Example:
    uv run python -m run.seed_gpw_snowflake --csv data/gpw_stocks.csv
    uv run python -m run.seed_gpw_snowflake --csv data/gpw_stocks.csv --write
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.gpw_backfill import normalize_securities

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("data/gpw_stocks.csv"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.csv.is_file():
        parser.error(f"Missing securities CSV: {args.csv}. Expected ticker,name columns")
    securities = normalize_securities(pd.read_csv(args.csv, dtype=str))
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select database.driver: snowflake in config/database.yaml")

    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            cur.execute('SELECT "SYMBOL", "ASSET_ID", "VENUE" FROM "SECURITIES"')
            rows = cur.fetchall()
            by_symbol: dict[str, tuple[int, str | None]] = {}
            for symbol, asset_id, venue in rows:
                if symbol in by_symbol:
                    raise RuntimeError(f"Duplicate reference security: {symbol}")
                by_symbol[str(symbol)] = (int(asset_id), venue)
            for symbol in securities["symbol"]:
                if symbol in by_symbol and by_symbol[symbol][1] != "GPW":
                    raise RuntimeError(
                        f"Security {symbol} already exists with venue {by_symbol[symbol][1]}"
                    )
            unseen = securities[~securities["symbol"].isin(by_symbol)]
            logger.info(
                "CSV symbols=%d, existing=%d, new=%d",
                len(securities),
                len(securities) - len(unseen),
                len(unseen),
            )
            if not args.write:
                logger.info(
                    "DRY RUN: no data written. Use --write to add %d securities.", len(unseen)
                )
                return
            if unseen.empty:
                logger.info("No new securities to seed")
                return

            # Initial, single-process seeding. IDs are not guaranteed safe with concurrent writers.
            try:
                cur.execute("BEGIN")
                cur.execute('SELECT COALESCE(MAX("ASSET_ID"), 0) FROM "SECURITIES"')
                next_id = int(cur.fetchone()[0])
                for row in unseen.itertuples(index=False):
                    next_id += 1
                    cur.execute(
                        'INSERT INTO "SECURITIES" ("ASSET_ID", "INSTRUMENT_CLASS", "MARKET_TYPE", '
                        '"NAME", "SYMBOL", "CURRENCY", "CALENDAR_ID", "INIT_DATE", "VENUE") '
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            next_id,
                            "stock",
                            "continuous",
                            row.name,
                            row.symbol,
                            "PLN",
                            0,
                            None,
                            "GPW",
                        ),
                    )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            logger.info("Inserted %d GPW securities", len(unseen))


if __name__ == "__main__":
    main()
