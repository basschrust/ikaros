"""Complete local Ikaros database, with candidate tables for every domain."""

from __future__ import annotations

from pathlib import Path

from database.candidate_observations import ensure_schema as ensure_fx_rate_candidates
from database.daedalus_reference import ensure_reference_schema
from database.duckdb_compat import CompatibleConnection
from database.duckdb_storage import Repository
from database.market_quote_schema import ensure_market_quote_schema


class LocalDuckDBRepository(Repository):
    """Initialize all local table families; no Snowflake module is imported here."""

    def __init__(self, *, output_dir: Path | str) -> None:
        super().__init__(output_dir=output_dir)

    def prepare_tables(self) -> None:
        super().prepare_tables()
        # New local files must retain decimal price precision rather than the
        # pre-1.0 DOUBLE base schema. Never rewrite a populated legacy table.
        for table in ("PRICES", "FX_RATES"):
            count = self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                continue
            existing = {
                str(column).upper(): str(datatype).upper()
                for column, datatype in self.connection.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE lower(table_name)=? AND table_schema=current_schema()",
                    (table.lower(),),
                ).fetchall()
            }
            for column in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
                target = "DECIMAL(38,0)" if column == "VOLUME" else "DECIMAL(24,8)"
                if existing.get(column) != target:
                    self.connection.execute(
                        f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE {target}'
                    )
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

        # The stock schema's older floating-point authoritative columns are
        # intentionally preserved on an existing database. New candidate rows
        # are already DECIMAL(24,8) and integer-volume.
