"""Reference-data queries for the generic market-candidate importer."""

from __future__ import annotations

from contextlib import closing
from datetime import date
from typing import Any


def eligible_derivative_contracts(
    connection: Any, *, since: date, backend: str = "snowflake"
) -> list[str]:
    """Only verified, registered futures/options with expiry within the window."""
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Unknown database backend: {backend}")
    bind = "%s" if backend == "snowflake" else "?"
    with closing(connection.cursor()) as cursor:
        cursor.execute(
            'SELECT "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS" '
            "WHERE \"INSTRUMENT_TYPE\" IN ('FUTURE','OPTION') "
            f'AND "EXPIRY_DATE" >= {bind}',
            (since.isoformat(),),
        )
        return [str(row[0]) for row in cursor.fetchall()]
