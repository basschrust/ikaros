"""Opt-in, read-only live Snowflake checks after the integer-key migration."""

from __future__ import annotations

import os

import pytest


def test_live_snowflake_integer_candidate_keys_and_provenance() -> None:
    if os.environ.get("IKAROS_TEST_SNOWFLAKE") != "1":
        pytest.skip("Enable explicitly with IKAROS_TEST_SNOWFLAKE=1")

    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        pytest.skip("Snowflake backend not configured")
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME IN "
                "('PRICES_CANDIDATES','PRICES') AND COLUMN_NAME='CANDIDATE_ID'"
            )
            types = dict(cur.fetchall())
            assert types == {"PRICES_CANDIDATES": "NUMBER", "PRICES": "NUMBER"}

            cur.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.SEQUENCES "
                "WHERE SEQUENCE_SCHEMA=CURRENT_SCHEMA() "
                "AND SEQUENCE_NAME='PRICES_CANDIDATES_ID_SEQ'"
            )
            assert int(cur.fetchone()[0]) == 1

            cur.execute(
                'SELECT COUNT(*) FROM (SELECT "CANDIDATE_ID" FROM "PRICES_CANDIDATES" '
                'GROUP BY "CANDIDATE_ID" HAVING COUNT(*)>1)'
            )
            assert int(cur.fetchone()[0]) == 0

            cur.execute(
                'SELECT COUNT(*) FROM "PRICES" p '
                'WHERE p."CANDIDATE_ID" IS NOT NULL '
                'AND NOT EXISTS (SELECT 1 FROM "PRICES_CANDIDATES" c '
                'WHERE c."CANDIDATE_ID"=p."CANDIDATE_ID")'
            )
            assert int(cur.fetchone()[0]) == 0
