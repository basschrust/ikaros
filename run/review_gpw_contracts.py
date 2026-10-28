"""Export a no-write registry review of current Snowflake DERIVATIVE_CANDIDATES."""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.derivatives_registry import review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--out", type=Path, default=Path("output/derivatives_registry_review.csv"))
    args = parser.parse_args()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select snowflake database driver")
    with repository_from_config(config) as repo:
        with closing(repo.connection.cursor()) as cur:
            cur.execute('SELECT "SYMBOL", "EXPIRY_DATE" FROM "DERIVATIVE_CANDIDATES" '
                        'WHERE "INSTRUMENT_TYPE" = %s ORDER BY "SYMBOL"', ("FUTURE",))
            rows = cur.fetchall()
    reviews = []
    for symbol, expiry in rows:
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        reviews.append(asdict(review(str(symbol), expiry, as_of=args.as_of)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    keys = list(COLUMNS)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(reviews)
    by_type = {}
    for entry in reviews:
        by_type[entry["category"]] = by_type.get(entry["category"], 0) + 1
    print(f"Reviewed {len(reviews)} candidates: {by_type}. Output: {args.out}")
    print("All entries intentionally require reference-data verification before promotion.")


COLUMNS = ("symbol", "family", "category", "underlying_hint", "contract_month",
           "reported_expiry", "third_friday", "expiry_consistent", "status", "reason")

if __name__ == "__main__":
    main()
