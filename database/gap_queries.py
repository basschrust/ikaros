"""Portable read-only database access for GPW stock and WIG20 gap detection."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from typing import Any, Sequence

DATASET_TABLES = {"WIG20": ("INDEX_PRICES", "INDEX_SYMBOL"), "GPW": ("PRICES", "SYMBOL")}


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _read(connection: Any, sql: str, params: tuple[object, ...]) -> list[tuple]:
    with closing(connection.cursor()) as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def session_coverage(
    connection: Any,
    *,
    dataset: str,
    symbols: Sequence[str],
    start: date,
    end: date,
    backend: str = "snowflake",
) -> tuple[dict[str, tuple[date, date]], dict[str, set[date]]]:
    """Return per-symbol history bounds and dates recorded in a requested window.

    SQL identifiers are selected from a fixed whitelist; values are bound.
    DuckDB uses question-mark binds, Snowflake uses percent-s binds.
    """
    if dataset not in DATASET_TABLES:
        raise ValueError(f"Unsupported gap dataset: {dataset}")
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Unsupported backend: {backend}")
    table, field = DATASET_TABLES[dataset]
    if not symbols:
        return {}, {}
    bind = "%s" if backend == "snowflake" else "?"
    placeholders = ",".join([bind] * len(symbols))
    field = '"' + field + '"'
    table = '"' + table + '"'
    extent_rows = _read(
        connection,
        f'SELECT {field}, MIN(CAST("DATE" AS DATE)), '
        f'MAX(CAST("DATE" AS DATE)) FROM {table} '
        f"WHERE {field} IN ({placeholders}) GROUP BY {field}",
        tuple(symbols),
    )
    extent = {str(symbol): (_date(first), _date(last)) for symbol, first, last in extent_rows}
    observed_rows = _read(
        connection,
        f'SELECT {field}, CAST("DATE" AS DATE) FROM {table} '
        f"WHERE {field} IN ({placeholders}) "
        f'AND CAST("DATE" AS DATE) BETWEEN {bind} AND {bind}',
        (*symbols, start.isoformat(), end.isoformat()),
    )
    observed: dict[str, set[date]] = defaultdict(set)
    for symbol, day in observed_rows:
        observed[str(symbol)].add(_date(day))
    return extent, dict(observed)
