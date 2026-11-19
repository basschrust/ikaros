"""Catalog, source-review and operational-report SQL for Ikaros jobs.

DB-specific identifiers, binding syntax and SQL live only in this module.
Public helpers return plain Python values instead of raw cursors.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
from typing import Any


def _fetch(connection: Any, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    with closing(connection.cursor()) as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def derivative_candidate_review_rows(connection: Any) -> list[tuple]:
    return _fetch(
        connection,
        'SELECT "SYMBOL", "INSTRUMENT_TYPE", "EXPIRY_DATE" '
        'FROM "DERIVATIVE_CANDIDATES" ORDER BY "INSTRUMENT_TYPE", "SYMBOL"',
    )


def scheduled_symbols(connection: Any, dataset: str, *, backend: str = "snowflake") -> list[str]:
    if backend not in {"snowflake", "duckdb"}:
        raise ValueError(f"Unsupported backend: {backend}")
    if dataset == "GPW":
        bind = "%s" if backend == "snowflake" else "?"
        sql = f'SELECT DISTINCT "SYMBOL" FROM "SECURITIES" WHERE "VENUE" = {bind} ORDER BY "SYMBOL"'
        params: tuple[object, ...] = ("GPW",)
    elif dataset == "FX":
        sql = (
            'SELECT DISTINCT "SYMBOL" FROM "CURRENCY_PAIRS" '
            'WHERE "IS_ACTIVE" = TRUE ORDER BY "SYMBOL"'
        )
        params = ()
    else:
        raise ValueError(f"Unsupported scheduled dataset: {dataset}")
    return [str(row[0]) for row in _fetch(connection, sql, params) if row[0]]


def iic_status_summary(
    connection: Any, *, dataset: str | None = None, failed_only: bool = False
) -> list[tuple]:
    filters: list[str] = []
    params: tuple[object, ...] = (dataset,) if dataset else ()
    if dataset:
        filters.append('"DATASET" = %s')
    if failed_only:
        filters.append("\"STATUS\" IN ('FAILED', 'NO_DATA')")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    return _fetch(
        connection,
        'SELECT "DATASET", "STATUS", COUNT(*), SUM("FETCHED_ROWS"), '
        'SUM("CANDIDATE_ROWS") FROM "IIC_BATCHES"'
        + where
        + ' GROUP BY "DATASET", "STATUS" ORDER BY "DATASET", "STATUS"',
        params,
    )


def iic_failed_batches(connection: Any, *, dataset: str | None = None) -> list[tuple]:
    return _fetch(
        connection,
        'SELECT "DATASET", "SYMBOL", "START_DATE", "END_DATE", '
        '"STATUS", "ATTEMPTS", "ERROR_MESSAGE" FROM "IIC_BATCHES" '
        "WHERE \"STATUS\" IN ('FAILED', 'NO_DATA')"
        + (' AND "DATASET" = %s' if dataset else "")
        + ' ORDER BY "UPDATED_AT" DESC LIMIT 100',
        (dataset,) if dataset else (),
    )


def snowflake_connection_snapshot(
    connection: Any, *, database: str, schema: str
) -> tuple[tuple[object, object, object], set[str]]:
    """Return connection metadata and schema table names (safe identifier checks)."""
    import re

    valid = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
    if not valid.fullmatch(database) or not valid.fullmatch(schema):
        raise ValueError("Invalid Snowflake database or schema name")
    with closing(connection.cursor()) as cursor:
        cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_ROLE()")
        meta = cursor.fetchone()
        cursor.execute(f'SHOW TABLES IN SCHEMA "{database}"."{schema}"')
        names = {str(row[1]).upper() for row in cursor.fetchall()}
    if not meta:
        raise RuntimeError("Snowflake connection metadata unavailable")
    return tuple(meta), names


def stored_fx_dates(
    connection: Any, *, pair_id: int, start: date, end: date, backend: str = "snowflake"
) -> set[date]:
    bind = "%s" if backend == "snowflake" else "?"
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Unsupported backend: {backend}")
    result = _fetch(
        connection,
        'SELECT CAST("DATE" AS DATE) FROM "FX_RATES" '
        f'WHERE "CURRENCY_PAIR_ID" = {bind} AND CAST("DATE" AS DATE) >= {bind} '
        f'AND CAST("DATE" AS DATE) <= {bind}',
        (pair_id, start, end),
    )
    return {
        value.date()
        if isinstance(value, datetime)
        else value
        if isinstance(value, date)
        else date.fromisoformat(str(value)[:10])
        for (value,) in result
    }
