"""Review discovered GPW futures and options without promoting them to contracts."""

from __future__ import annotations
import argparse
import csv
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.derivative_review_ext import review_candidate

COLUMNS = (
    "symbol",
    "family",
    "category",
    "underlying_hint",
    "contract_month",
    "reported_expiry",
    "third_friday",
    "expiry_consistent",
    "status",
    "reason",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--out", type=Path, default=Path("output/derivatives_registry_review.csv"))
    args = parser.parse_args()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake database driver")
    with repository_from_config(config) as repo:
        with closing(repo.connection.cursor()) as cur:
            cur.execute(
                'SELECT "SYMBOL", "INSTRUMENT_TYPE", "EXPIRY_DATE" '
                'FROM "DERIVATIVE_CANDIDATES" ORDER BY "INSTRUMENT_TYPE", "SYMBOL"'
            )
            records = cur.fetchall()
    results = []
    for symbol, instrument_type, expiry in records:
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        results.append(
            review_candidate(str(symbol), str(instrument_type), expiry, as_of=args.as_of)
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    print(f"Reviewed {len(results)} candidate rows -> {args.out}")
    print("Candidates remain unverified; no contract/reference prices were written.")


if __name__ == "__main__":
    main()
