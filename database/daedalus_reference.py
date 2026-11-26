"""Additive local DuckDB reference schema needed by Daedalus.

Never drops tables, rewrites existing authoritative prices, or manufactures contracts.
"""

from __future__ import annotations

from typing import Any

CONTRACT_TYPES: dict[str, str] = {
    "SYMBOL": "VARCHAR",
    "CURRENCY": "VARCHAR",
    "MULTIPLIER": "DECIMAL(18,4)",
    "STRIKE": "DECIMAL(24,8)",
    "OPTION_SIDE": "VARCHAR",
    "SOURCE": "VARCHAR",
}


def ensure_reference_schema(connection: Any) -> None:
    """Create missing index membership and extend the legacy four-field contract registry."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS "INDEX_CONSTITUENTS" (
            "INDEX_SYMBOL" VARCHAR NOT NULL,
            "SECURITY_SYMBOL" VARCHAR NOT NULL,
            "WEIGHT" DECIMAL(24,12) NOT NULL,
            "VALID_FROM" DATE NOT NULL,
            "VALID_TO" DATE,
            "SOURCE" VARCHAR NOT NULL
        )"""
    )
    # Local bootstrap creates the original 4-column DERIVATIVE_CONTRACTS first.
    present = {
        str(name).upper()
        for (name,) in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE lower(table_name)='derivative_contracts' "
            "AND table_schema=current_schema()"
        ).fetchall()
    }
    if not present:
        raise RuntimeError("DERIVATIVE_CONTRACTS missing; initialize Ikaros first")
    for column, sqltype in CONTRACT_TYPES.items():
        if column not in present:
            connection.execute(
                f'ALTER TABLE "DERIVATIVE_CONTRACTS" ADD COLUMN "{column}" {sqltype}'
            )
