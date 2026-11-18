"""Optional real Snowflake integration smoke test (repository init prepares tables).

Run explicitly with IKAROS_TEST_SNOWFLAKE=1, after configuring credentials.
The default make test never connects to a live Snowflake account.
"""

from __future__ import annotations

import os

import pytest


def test_live_snowflake_gpwsymbol_schema_and_references():
    if os.environ.get("IKAROS_TEST_SNOWFLAKE") != "1":
        pytest.skip("Set IKAROS_TEST_SNOWFLAKE=1 to test connected Snowflake")
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        pytest.skip("Snowflake backend not selected")
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cursor:
            for table in (
                "PROVIDERS",
                "PROVIDER_SYMBOLS",
                "PRICES_CANDIDATES",
                "PRICES",
                "SECURITIES",
            ):
                cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                count = cursor.fetchone()[0]
                assert count >= 0
            cursor.execute("""SELECT COUNT(*) FROM "PROVIDER_SYMBOLS" ps
                LEFT JOIN "SECURITIES" s ON s."ASSET_ID"=ps."ASSET_ID"
                LEFT JOIN "PROVIDERS" p ON p."PROVIDER_ID"=ps."PROVIDER_ID"
                WHERE s."ASSET_ID" IS NULL OR p."PROVIDER_ID" IS NULL""")
            assert cursor.fetchone()[0] == 0
