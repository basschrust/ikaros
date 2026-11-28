"""Complete local Ikaros database, with candidate tables for every domain."""

from __future__ import annotations

from pathlib import Path

from database.candidate_observations import ensure_schema as ensure_fx_rate_candidates
from database.daedalus_reference import ensure_reference_schema
from database.duckdb_compat import CompatibleConnection
from database.duckdb_storage import Repository
from database.market_quote_schema import ensure_market_quote_schema
from database.numeric_contract import classify


class LocalDuckDBRepository(Repository):
    """Initialize all local table families; no Snowflake module is imported here."""

    def __init__(self, *, output_dir: Path | str) -> None:
        super().__init__(output_dir=output_dir)

    def prepare_tables(self) -> None:
        super().prepare_tables()
        # database.schema now creates fixed-point types on every fresh install.
        # Do not silently coerce historic FLOAT/DOUBLE prices: this needs an
        # explicit, checkpointed backup and a preflight of existing values.
        self.connection = CompatibleConnection(self.connection)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS "DERIVATIVE_CONTRACTS" (
               "CONTRACT_ID" VARCHAR PRIMARY KEY,
               "INSTRUMENT_TYPE" VARCHAR NOT NULL,
               "UNDERLYING_SYMBOL" VARCHAR,
               "EXPIRY_DATE" DATE NOT NULL,
               "VENUE" VARCHAR)"""
        )
        # Applies an additive migration for existing 4-column registries too.
        ensure_reference_schema(self.connection)
        ensure_market_quote_schema(self.connection, backend="duckdb")
        ensure_fx_rate_candidates(self.connection, backend="duckdb")

        existing = self.connection.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema=current_schema()"
        ).fetchall()
        incompatible = [
            f"{item.table}.{item.name} ({item.actual})"
            for item in classify([(str(t), str(c), str(d)) for t, c, d in existing])
            if item.unknown_float or item.unknown_storage or item.needs_conversion
        ]
        if incompatible:
            sample = ", ".join(incompatible[:6])
            raise RuntimeError(
                "Local market database has non-compliant persisted numeric columns: "
                f"{sample}. Audit and migrate with: python -m run.numeric_storage "
                f"--database '{self.localdb}' --write. First run without --write "
                "to inspect the plan; close DBeaver before migration."
            )
