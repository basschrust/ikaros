"""Create and verify the Ikaros Snowflake schema.

Run from Ikaros repository root:
    python -m run.check_snowflake_tables

Requires SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD and
    pip install -r requirements-snowflake.txt
"""

from __future__ import annotations

import os
import sys

from config.config import load_config
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

    # Constructor is responsible for CREATE SCHEMA/TABLE IF NOT EXISTS.
    repository = repository_from_config(config)
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_ROLE()")
            db, schema, role = cursor.fetchone()
            print(f"Snowflake connected: database={db}, schema={schema}, role={role}")
            cursor.execute(f'SHOW TABLES IN SCHEMA "{repository.database}"."{repository.schema}"')
            names = {str(row[1]).upper() for row in cursor.fetchall()}
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
