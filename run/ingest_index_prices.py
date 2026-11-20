"""Load validated index OHLC snapshots into Snowflake; dry-run unless --write."""

from __future__ import annotations

import argparse
from pathlib import Path
from ingestion.index_prices import read_index_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("data/wig20_eod_2026_10.csv"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    rows = read_index_csv(args.csv)
    print(
        f"Validated {len(rows)} rows; symbols={sorted({r[0] for r in rows})}; "
        f"dates={rows[0][1]}..{rows[-1][1]}"
    )
    if not args.write:
        print("DRY RUN: rerun with --write to persist")
        return

    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select database.driver: snowflake")
    with repository_from_config(config) as repo:
        from database.isolated.run_ingest_index_prices import _inline_main_52

        _inline_main_52(repo, rows)
    print(f"Upserted {len(rows)} index quotations into INDEX_PRICES")


if __name__ == "__main__":
    main()
