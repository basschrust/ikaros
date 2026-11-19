"""Create and verify the Ikaros Snowflake schema.

Run from Ikaros repository root: python -m run.check_snowflake_tables
"""

from __future__ import annotations

import os
import sys

from config.config import load_config
from database.catalog_queries import snowflake_connection_snapshot
from database.repositories.factory import repository_from_config
from database.snowflake_storage import COLUMNS


def main() -> int:
    config = load_config("database.yaml")
    settings = config.get("database", {})
    if settings.get("driver", "duckdb").lower() != "snowflake":
        print("ERROR: config/database.yaml database.driver must be 'snowflake'", file=sys.stderr)
        return 2
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER")
    missing = [name for name in required if not os.getenv(name)]
    sf_cfg = settings.get("snowflake", {})
    if sf_cfg.get("authenticator", "snowflake") == "snowflake" and not os.getenv(
        "SNOWFLAKE_PASSWORD"
    ):
        missing.append("SNOWFLAKE_PASSWORD")
    if missing:
        print("ERROR: missing environment variables: " + ", ".join(missing), file=sys.stderr)
        return 2
    repository = repository_from_config(config)
    try:
        (db, schema, role), names = snowflake_connection_snapshot(
            repository.connection, database=repository.database, schema=repository.schema
        )
        print(f"Snowflake connected: database={db}, schema={schema}, role={role}")
        expected = {table.upper() for table in COLUMNS}
        for table in sorted(expected):
            print(f"{'OK' if table in names else 'MISSING'}  {table}")
        missing_tables = expected - names
        if missing_tables:
            print("ERROR: missing tables: " + ", ".join(sorted(missing_tables)), file=sys.stderr)
            return 1
        print(f"PASS: all {len(expected)} Ikaros tables are present.")
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
