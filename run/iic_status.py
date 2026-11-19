"""Report batch status from Snowflake (read-only)."""

from __future__ import annotations

import argparse

from config.config import load_config
from database.catalog_queries import iic_failed_batches, iic_status_summary
from database.repositories.factory import repository_from_config


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=["GPW", "FX"], default=None)
    p.add_argument("--failed-only", action="store_true")
    args = p.parse_args()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Set database.driver: snowflake")
    with repository_from_config(config) as repo:
        from ingestion.iic import SnowflakeIIC

        SnowflakeIIC(repo.connection).prepare()
        for row in iic_status_summary(
            repo.connection, dataset=args.dataset, failed_only=args.failed_only
        ):
            print(*row, sep=" | ")
        if args.failed_only:
            for row in iic_failed_batches(repo.connection, dataset=args.dataset):
                print(*row, sep=" | ")


if __name__ == "__main__":
    main()
