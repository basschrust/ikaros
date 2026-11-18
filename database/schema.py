"""Single schema authority for Ikaros DuckDB and Snowflake (pre-1.0).

prepare_tables() creates the complete current schema on fresh instances and
extends pre-existing instances without dropping or modifying stored candles.
"""

from __future__ import annotations
from typing import Any, Literal

Backend = Literal["duckdb", "snowflake"]

BASE_COLUMNS: dict[str, dict[str, str]] = {
    "calendars": {
        "calendar_id": "BIGINT",
        "name": "VARCHAR",
        "days_per_year": "BIGINT",
        "init_date": "TIMESTAMP",
        "working_days": "VARCHAR",
        "comment": "VARCHAR",
    },
    "trading_days": {
        "date": "TIMESTAMP",
        "calendar_id": "BIGINT",
        "comment": "VARCHAR",
        "load_status": "VARCHAR",
        "load_date": "TIMESTAMP",
    },
    "non_trading_days": {"date": "TIMESTAMP", "calendar_id": "BIGINT", "comment": "VARCHAR"},
    "securities": {
        "asset_id": "BIGINT",
        "instrument_class": "VARCHAR",
        "market_type": "VARCHAR",
        "name": "VARCHAR",
        "reference_price": "DOUBLE",
        "symbol": "VARCHAR",
        "currency": "VARCHAR",
        "calendar_id": "BIGINT",
        "init_date": "TIMESTAMP",
        "venue": "VARCHAR",
        "is_active": "BOOLEAN DEFAULT TRUE",
    },
    "prices": {
        "symbol": "VARCHAR",
        "date": "TIMESTAMP",
        "open": "DOUBLE",
        "high": "DOUBLE",
        "low": "DOUBLE",
        "close": "DOUBLE",
        "volume": "DOUBLE",
        "dividend": "DOUBLE",
        "split_ratio": "DOUBLE",
        "asset_id": "BIGINT",
        "provider_id": "BIGINT",
        "candidate_id": "VARCHAR(64)",
        "selected_at": "TIMESTAMP",
        "selection_reason": "VARCHAR",
    },
    "currencies": {
        "currency_id": "BIGINT",
        "symbol": "VARCHAR",
        "calendar_id": "BIGINT",
        "name": "VARCHAR",
        "comment": "VARCHAR",
        "code": "VARCHAR",
        "minor_unit": "BIGINT",
        "is_active": "BOOLEAN DEFAULT TRUE",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "currency_pairs": {
        "currency_pair_id": "BIGINT",
        "base_currency_id": "BIGINT",
        "quote_currency_id": "BIGINT",
        "symbol": "VARCHAR",
        "is_active": "BOOLEAN DEFAULT TRUE",
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
    "fx_rates": {
        "currency_pair_id": "BIGINT",
        "symbol": "VARCHAR",
        "date": "TIMESTAMP",
        "open": "DOUBLE",
        "high": "DOUBLE",
        "low": "DOUBLE",
        "close": "DOUBLE",
        "volume": "DOUBLE",
        "comment": "VARCHAR",
        "ingested_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    },
}

NEW_COLUMNS: dict[str, dict[str, str]] = {
    "providers": {
        "provider_id": "BIGINT PRIMARY KEY",
        "provider_code": "VARCHAR(64) NOT NULL UNIQUE",
        "name": "VARCHAR NOT NULL",
        "priority": "INTEGER NOT NULL",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "provider_symbols": {
        "provider_symbol_id": "BIGINT PRIMARY KEY",
        "asset_id": "BIGINT NOT NULL",
        "provider_id": "BIGINT NOT NULL",
        "provider_symbol": "VARCHAR(128) NOT NULL",
        "valid_from": "DATE NOT NULL",
        "valid_to": "DATE",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "created_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "prices_candidates": {
        "candidate_id": "VARCHAR(64) PRIMARY KEY",
        "asset_id": "BIGINT NOT NULL",
        "provider_id": "BIGINT NOT NULL",
        "provider_symbol_id": "BIGINT NOT NULL",
        "symbol": "VARCHAR(128) NOT NULL",
        "date": "DATE NOT NULL",
        "open": "DECIMAL(24,8)",
        "high": "DECIMAL(24,8)",
        "low": "DECIMAL(24,8)",
        "close": "DECIMAL(24,8) NOT NULL",
        "volume": "DECIMAL(38,0)",
        "dividend": "DECIMAL(24,8)",
        "split_ratio": "DECIMAL(24,8)",
        "validation_status": "VARCHAR(16) NOT NULL DEFAULT 'PENDING'",
        "validation_reason": "VARCHAR",
        "source_timestamp": "TIMESTAMP",
        "ingested_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
}


def _type(sql: str, backend: Backend) -> str:
    """Translate SQL *type tokens*, not substrings in default expressions."""
    if backend == "snowflake":
        import re

        sql = re.sub(r"\bBIGINT\b", "NUMBER(38,0)", sql)
        sql = re.sub(r"\bDECIMAL\(", "NUMBER(", sql)
        sql = re.sub(r"\bTIMESTAMP\b(?!\s*\()", "TIMESTAMP_NTZ", sql, count=1)
        # The single TIMESTAMP token is always the leading column type in
        # our declarations. CURRENT_TIMESTAMP must stay a valid SQL function.
        return sql
    return sql


def _execute(conn: Any, sql: str) -> None:
    conn.execute(sql) if hasattr(conn, "execute") else _cursor_execute(conn, sql)


def _cursor_execute(conn: Any, sql: str) -> None:
    with conn.cursor() as cur:
        cur.execute(sql)


def _fetch(conn: Any, sql: str) -> list[tuple[Any, ...]]:
    if hasattr(conn, "execute"):
        return list(conn.execute(sql).fetchall())
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def prepare_tables(connection: Any, *, backend: Backend) -> None:
    """Create all 11 tables; extend only new columns on existing tables."""
    if backend not in ("duckdb", "snowflake"):
        raise ValueError(f"Unknown backend: {backend}")
    for table, columns in BASE_COLUMNS.items():
        fields = ", ".join(f'"{k.upper()}" {_type(v, backend)}' for k, v in columns.items())
        _execute(connection, f'CREATE TABLE IF NOT EXISTS "{table.upper()}" ({fields})')
    # Explicitly inspect existing columns. Snowflake can reject
    # ALTER TABLE ... ADD COLUMN IF NOT EXISTS for an existing column with
    # SQL compilation error "ambiguous column name"; skip the DDL instead.
    for table in ("securities", "prices"):
        added = (
            ("is_active",)
            if table == "securities"
            else ("asset_id", "provider_id", "candidate_id", "selected_at", "selection_reason")
        )
        if backend == "snowflake":
            columns_sql = (
                'SELECT "COLUMN_NAME" FROM INFORMATION_SCHEMA.COLUMNS '
                f"WHERE TABLE_CATALOG = CURRENT_DATABASE() AND "
                f"TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME = '{table.upper()}'"
            )
        else:
            columns_sql = (
                "SELECT column_name FROM information_schema.columns "
                f"WHERE lower(table_name) = '{table}' "
                "AND table_schema = current_schema()"
            )
        existing = {str(row[0]).upper() for row in _fetch(connection, columns_sql)}
        for field in added:
            if field.upper() not in existing:
                _execute(
                    connection,
                    f'ALTER TABLE "{table.upper()}" ADD COLUMN "{field.upper()}" '
                    f"{_type(BASE_COLUMNS[table][field], backend)}",
                )
    conflicts = _fetch(
        connection,
        'SELECT "ASSET_ID", COUNT(*) FROM "SECURITIES" GROUP BY "ASSET_ID" HAVING "ASSET_ID" IS NULL OR COUNT(*) > 1 LIMIT 5',
    )
    if conflicts:
        raise ValueError(f"SECURITIES.ASSET_ID must be non-null and unique: {conflicts!r}")
    if backend == "duckdb":
        rows = _fetch(
            connection,
            "SELECT COUNT(*) FROM duckdb_constraints() WHERE lower(table_name)='securities' AND constraint_type='PRIMARY KEY'",
        )
        if not rows[0][0]:
            _execute(connection, 'ALTER TABLE "SECURITIES" ADD PRIMARY KEY ("ASSET_ID")')
    else:
        rows = _fetch(
            connection,
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME='SECURITIES' AND CONSTRAINT_TYPE='PRIMARY KEY'",
        )
        if not rows[0][0]:
            _execute(
                connection,
                'ALTER TABLE "SECURITIES" ADD CONSTRAINT "PK_SECURITIES_ASSET_ID" PRIMARY KEY ("ASSET_ID")',
            )
    constraints = {
        "provider_symbols": [
            'FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID")',
            'FOREIGN KEY ("PROVIDER_ID") REFERENCES "PROVIDERS"("PROVIDER_ID")',
            'CHECK ("VALID_TO" IS NULL OR "VALID_TO" >= "VALID_FROM")',
        ],
        "prices_candidates": [
            'FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID")',
            'FOREIGN KEY ("PROVIDER_ID") REFERENCES "PROVIDERS"("PROVIDER_ID")',
            'FOREIGN KEY ("PROVIDER_SYMBOL_ID") REFERENCES "PROVIDER_SYMBOLS"("PROVIDER_SYMBOL_ID")',
            "CHECK (\"VALIDATION_STATUS\" IN ('PENDING','VALID','REJECTED','REVIEW'))",
        ],
    }
    for table, columns in NEW_COLUMNS.items():
        fields = [f'"{k.upper()}" {_type(v, backend)}' for k, v in columns.items()]
        fields.extend(
            c
            for c in constraints.get(table, [])
            if backend == "duckdb" or not c.startswith("CHECK")
        )
        _execute(connection, f'CREATE TABLE IF NOT EXISTS "{table.upper()}" ({", ".join(fields)})')
    _execute(
        connection,
        """UPDATE "PRICES" p SET "ASSET_ID" = (
        SELECT MIN(s."ASSET_ID") FROM "SECURITIES" s WHERE s."SYMBOL" = p."SYMBOL"
    ) WHERE p."ASSET_ID" IS NULL AND (
        SELECT COUNT(*) FROM "SECURITIES" s WHERE s."SYMBOL" = p."SYMBOL"
    ) = 1""",
    )


def diagnostics(connection: Any) -> dict[str, int]:
    """Post-upgrade reconciliation counts; does not change data."""
    checks = {
        "unmapped_prices": 'SELECT COUNT(*) FROM "PRICES" WHERE "ASSET_ID" IS NULL',
        "duplicate_asset_dates": 'SELECT COUNT(*) FROM (SELECT "ASSET_ID", CAST("DATE" AS DATE) FROM "PRICES" WHERE "ASSET_ID" IS NOT NULL GROUP BY "ASSET_ID",CAST("DATE" AS DATE) HAVING COUNT(*) > 1)',
        "unprovenanced_prices": 'SELECT COUNT(*) FROM "PRICES" WHERE "PROVIDER_ID" IS NULL OR "CANDIDATE_ID" IS NULL',
    }
    return {name: int(_fetch(connection, sql)[0][0]) for name, sql in checks.items()}
