"""Report batch status from Snowflake (read-only)."""
from __future__ import annotations

import argparse
from contextlib import closing
from config.config import load_config
from database.repositories.factory import repository_from_config


def main():
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
        filters = []
        params = []
        if args.dataset:
            filters.append('"DATASET" = %s')
            params.append(args.dataset)
        if args.failed_only:
            filters.append('"STATUS" IN (\'FAILED\', \'NO_DATA\')')
        where = " WHERE " + " AND ".join(filters) if filters else ""
        query = (
            'SELECT "DATASET", "STATUS", COUNT(*), SUM("FETCHED_ROWS"), '
            'SUM("CANDIDATE_ROWS") FROM "IIC_BATCHES"' + where +
            ' GROUP BY "DATASET", "STATUS" ORDER BY "DATASET", "STATUS"'
        )
        with closing(repo.connection.cursor()) as cur:
            cur.execute(query, tuple(params))
            for row in cur.fetchall():
                print(*row, sep=" | ")
        if args.failed_only:
            with closing(repo.connection.cursor()) as cur:
                cur.execute(
                    'SELECT "DATASET", "SYMBOL", "START_DATE", "END_DATE", '
                    '"STATUS", "ATTEMPTS", "ERROR_MESSAGE" FROM "IIC_BATCHES" '
                    'WHERE "STATUS" IN (\'FAILED\', \'NO_DATA\')'
                    + (' AND "DATASET" = %s' if args.dataset else '')
                    + ' ORDER BY "UPDATED_AT" DESC LIMIT 100',
                    (args.dataset,) if args.dataset else (),
                )
                for row in cur.fetchall():
                    print(*row, sep=" | ")


if __name__ == "__main__":
    main()
