"""Schema authority for WIG20 index and GPW derivative multi-source observations.

Run separately from legacy schema initialization until the INDEX_PRICES and
DERIVATIVE_PRICES owners are consolidated; never replace historic price tables.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Literal

Backend = Literal["snowflake", "duckdb"]
KINDS = ("INDEX", "DERIVATIVE")


def _exec(connection: Any, sql: str) -> None:
    with closing(connection.cursor()) as cur:
        cur.execute(sql)


def _rows(connection: Any, sql: str) -> list[tuple]:
    with closing(connection.cursor()) as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _columns(connection: Any, table: str, backend: Backend) -> set[str]:
    if backend == "snowflake":
        sql = (
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG=CURRENT_DATABASE() AND "
            f"TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME='{table}'"
        )
    else:
        sql = (
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE LOWER(TABLE_NAME)='{table.lower()}' AND TABLE_SCHEMA=CURRENT_SCHEMA()"
        )
    return {str(row[0]).upper() for row in _rows(connection, sql)}


def ensure_market_quote_schema(connection: Any, *, backend: Backend) -> None:
    """Idempotent additive DDL: mapping/candidate tables and price provenance.

    Creates minimal authoritative tables only when absent; never rewrites existing rows.
    """
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Unsupported backend {backend}")
    integer = "NUMBER(38,0)" if backend == "snowflake" else "BIGINT"
    decimal = "NUMBER(24,8)" if backend == "snowflake" else "DECIMAL(24,8)"
    volume = "NUMBER(38,0)" if backend == "snowflake" else "DECIMAL(38,0)"
    stamp = "TIMESTAMP_NTZ" if backend == "snowflake" else "TIMESTAMP"
    # These are the stable minimal domain tables used by the existing
    # specialized importers. CREATE IF NOT EXISTS never replaces legacy rows.
    _exec(
        connection,
        f"""CREATE TABLE IF NOT EXISTS "INDEX_PRICES" (
        "INDEX_SYMBOL" VARCHAR(128) NOT NULL, "DATE" DATE NOT NULL,
        "OPEN" {decimal}, "HIGH" {decimal}, "LOW" {decimal}, "CLOSE" {decimal},
        "SOURCE" VARCHAR(128), "CANDIDATE_ID" {integer},
        "PROVIDER_ID" {integer}, "SELECTION_REASON" VARCHAR(128),
        "SELECTED_AT" {stamp})""",
    )
    _exec(
        connection,
        f"""CREATE TABLE IF NOT EXISTS "DERIVATIVE_PRICES" (
        "CONTRACT_ID" VARCHAR(128) NOT NULL, "DATE" DATE NOT NULL,
        "OPEN" {decimal}, "HIGH" {decimal}, "LOW" {decimal}, "CLOSE" {decimal},
        "VOLUME" {volume}, "OPEN_INTEREST" {volume}, "SETTLEMENT" {decimal},
        "SOURCE" VARCHAR(128), "INGESTED_AT" {stamp} DEFAULT CURRENT_TIMESTAMP,
        "CANDIDATE_ID" {integer}, "PROVIDER_ID" {integer},
        "SELECTION_REASON" VARCHAR(128), "SELECTED_AT" {stamp})""",
    )
    for kind in KINDS:
        prefix = "INDEX" if kind == "INDEX" else "DERIVATIVE"
        key = "INDEX_SYMBOL" if kind == "INDEX" else "CONTRACT_ID"
        mapping_table = f"{prefix}_PROVIDER_SYMBOLS"
        candidate_table = f"{prefix}_PRICE_CANDIDATES"
        _exec(
            connection,
            f'CREATE SEQUENCE IF NOT EXISTS "{mapping_table}_ID_SEQ" START WITH 1 INCREMENT BY 1',
        )
        _exec(
            connection,
            f'CREATE SEQUENCE IF NOT EXISTS "{candidate_table}_ID_SEQ" START WITH 1 INCREMENT BY 1',
        )
        _exec(
            connection,
            f'''CREATE TABLE IF NOT EXISTS "{mapping_table}" (
            "PROVIDER_SYMBOL_ID" {integer} PRIMARY KEY,
            "{key}" VARCHAR(128) NOT NULL,
            "PROVIDER_ID" {integer} NOT NULL,
            "PROVIDER_SYMBOL" VARCHAR(128) NOT NULL,
            "VALID_FROM" DATE NOT NULL,
            "VALID_TO" DATE,
            "IS_ACTIVE" BOOLEAN NOT NULL DEFAULT TRUE,
            "CREATED_AT" {stamp} DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY ("PROVIDER_ID") REFERENCES "PROVIDERS"("PROVIDER_ID")
        )''',
        )
        _exec(
            connection,
            f'''CREATE TABLE IF NOT EXISTS "{candidate_table}" (
            "CANDIDATE_ID" {integer} PRIMARY KEY,
            "CONTENT_HASH" VARCHAR(64) NOT NULL UNIQUE,
            "{key}" VARCHAR(128) NOT NULL,
            "PROVIDER_ID" {integer} NOT NULL,
            "PROVIDER_SYMBOL_ID" {integer} NOT NULL,
            "DATE" DATE NOT NULL,
            "OPEN" {decimal}, "HIGH" {decimal}, "LOW" {decimal},
            "CLOSE" {decimal}, "VOLUME" {volume},
            "SETTLEMENT" {decimal}, "OPEN_INTEREST" {volume},
            "VALIDATION_STATUS" VARCHAR(16) NOT NULL,
            "VALIDATION_REASON" VARCHAR(128),
            "INGESTED_AT" {stamp} DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY ("PROVIDER_ID") REFERENCES "PROVIDERS"("PROVIDER_ID"),
            FOREIGN KEY ("PROVIDER_SYMBOL_ID") REFERENCES "{mapping_table}"("PROVIDER_SYMBOL_ID")
        )''',
        )
        table = f"{prefix}_PRICES"
        if not _columns(connection, table, backend):
            # The owners of these legacy tables are specialized importers.
            # Never create incomplete placeholder authoritative tables.
            continue
        additions = {
            "CANDIDATE_ID": integer,
            "PROVIDER_ID": integer,
            "SELECTION_REASON": "VARCHAR(128)",
            "SELECTED_AT": stamp,
        }
        existing = _columns(connection, table, backend)
        for column, declaration in additions.items():
            if column not in existing:
                _exec(connection, f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')
