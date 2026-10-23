"""Cloud persistence matching Ikaros' current DuckDB Repository API.

Adapted from Daedalus' Snowflake connector design. Uses a persistent shared
schema, not a per-simulation schema. Credentials are read from environment.

This first-stage adapter preserves the existing append-only semantics; MERGE,
checkpointing, and ingestion metadata belong in the next reliability phase.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

import pandas as pd

# Optional dependency: importing the local factory does not require Snowflake.

COLUMNS: dict[str, dict[str, str]] = {
    "calendars": {"calendar_id": "NUMBER(38,0)", "name": "VARCHAR", "days_per_year": "NUMBER(38,0)", "init_date": "TIMESTAMP_NTZ", "working_days": "VARCHAR", "comment": "VARCHAR"},
    "trading_days": {"date": "TIMESTAMP_NTZ", "calendar_id": "NUMBER(38,0)", "comment": "VARCHAR", "load_status": "VARCHAR", "load_date": "TIMESTAMP_NTZ"},
    "non_trading_days": {"date": "TIMESTAMP_NTZ", "calendar_id": "NUMBER(38,0)", "comment": "VARCHAR"},
    "securities": {"asset_id": "NUMBER(38,0)", "instrument_class": "VARCHAR", "market_type": "VARCHAR", "name": "VARCHAR", "reference_price": "FLOAT", "symbol": "VARCHAR", "currency": "VARCHAR", "calendar_id": "NUMBER(38,0)", "init_date": "TIMESTAMP_NTZ", "venue": "VARCHAR"},
    "prices": {"symbol": "VARCHAR", "date": "TIMESTAMP_NTZ", "open": "FLOAT", "high": "FLOAT", "low": "FLOAT", "close": "FLOAT", "volume": "FLOAT", "dividend": "FLOAT", "split_ratio": "FLOAT"},
    "currencies": {"currency_id": "NUMBER(38,0)", "symbol": "VARCHAR", "calendar_id": "NUMBER(38,0)", "name": "VARCHAR", "comment": "VARCHAR", "code": "VARCHAR", "minor_unit": "NUMBER(38,0)", "is_active": "BOOLEAN", "created_at": "TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()"},
    "currency_pairs": {"currency_pair_id": "NUMBER(38,0)", "base_currency_id": "NUMBER(38,0)", "quote_currency_id": "NUMBER(38,0)", "symbol": "VARCHAR", "is_active": "BOOLEAN", "created_at": "TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()"},
    "fx_rates": {"currency_pair_id": "NUMBER(38,0)", "symbol": "VARCHAR", "date": "TIMESTAMP_NTZ", "open": "FLOAT", "high": "FLOAT", "low": "FLOAT", "close": "FLOAT", "volume": "FLOAT", "comment": "VARCHAR", "ingested_at": "TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()"},
}

WRITE_COLUMNS = {
    "calendars": ["calendar_id", "name", "days_per_year", "init_date", "working_days", "comment"],
    "trading_days": ["date", "calendar_id", "comment", "load_status", "load_date"],
    "non_trading_days": ["date", "calendar_id", "comment"],
    "securities": ["asset_id", "instrument_class", "market_type", "name", "symbol", "currency", "calendar_id", "init_date", "venue"],
    "currencies": ["currency_id", "symbol", "calendar_id", "name", "comment", "code", "minor_unit", "is_active"],
    "currency_pairs": ["currency_pair_id", "base_currency_id", "quote_currency_id", "symbol", "is_active"],
    "prices": ["symbol", "date", "open", "high", "low", "close", "volume", "dividend", "split_ratio"],
    "fx_rates": ["currency_pair_id", "symbol", "date", "open", "high", "low", "close", "volume"],
}

_VALID_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _ident(s: str) -> str:
    if not _VALID_IDENTIFIER.fullmatch(s):
        raise ValueError(f"Invalid SQL identifier: {s!r}")
    return s.upper()


class SnowflakeRepository:
    def __init__(self, *, database: str = "DAEDALUS", schema: str = "MARKET_DATA",
                 warehouse: str = "DAEDALUS_WH", account: str | None = None,
                 user: str | None = None, password: str | None = None,
                 authenticator: str = "snowflake", role: str | None = None):
        import snowflake.connector

        self.database, self.schema, self.warehouse = map(_ident, (database, schema, warehouse))
        kwargs: dict[str, Any] = {
            "account": account or os.environ["SNOWFLAKE_ACCOUNT"],
            "user": user or os.environ["SNOWFLAKE_USER"],
            "warehouse": self.warehouse,
            "database": self.database,
            "authenticator": authenticator,
        }
        if role:
            kwargs["role"] = _ident(role)
        if authenticator == "snowflake":
            secret = password or os.environ.get("SNOWFLAKE_PASSWORD")
            if not secret:
                raise ValueError("SNOWFLAKE_PASSWORD is required for password authentication")
            kwargs["password"] = secret
        self.connection = snowflake.connector.connect(**kwargs)
        try:
            self._execute(f'CREATE SCHEMA IF NOT EXISTS "{self.database}"."{self.schema}"')
            self._execute(f'USE SCHEMA "{self.database}"."{self.schema}"')
            self.prepare_tables()
        except Exception:
            self.close()
            raise

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connection.cursor() as cur:
            cur.execute(sql, params)

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
        with self.connection.cursor() as cur:
            cur.execute(sql, params)
            frame = cur.fetch_pandas_all()
            frame.columns = [str(column).lower() for column in frame.columns]
            return frame

    def prepare_tables(self) -> None:
        for table, columns in COLUMNS.items():
            definition = ", ".join(f'"{_ident(k)}" {typ}' for k, typ in columns.items())
            self._execute(f'CREATE TABLE IF NOT EXISTS "{_ident(table)}" ({definition})')

    def _insert_frame(self, table: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        from snowflake.connector.pandas_tools import write_pandas

        fields = WRITE_COLUMNS[table]
        absent = set(fields) - set(df.columns)
        if absent:
            raise ValueError(f"{table}: missing columns {sorted(absent)}")
        frame = df[fields].copy()
        frame.columns = [col.upper() for col in fields]
        for field in fields:
            if field in ("date", "init_date", "load_date"):
                frame[field.upper()] = pd.to_datetime(frame[field.upper()], errors="coerce")
        # Arrow-backed write_pandas requires consistent datatypes and column casing.
        success, _chunks, count, _output = write_pandas(
            self.connection, frame, table_name=_ident(table),
            database=self.database, schema=self.schema, quote_identifiers=True,
        )
        if not success or count != len(frame):
            raise RuntimeError(f"Snowflake write_pandas failed for {table}: {count}/{len(frame)} rows")

    def save_calendars(self, calendars_df: pd.DataFrame) -> None:
        self._insert_frame("calendars", calendars_df)

    def save_trading_days(self, trading_days_df: pd.DataFrame) -> None:
        self._insert_frame("trading_days", trading_days_df)

    def save_non_trading_days(self, non_trading_days_df: pd.DataFrame) -> None:
        self._insert_frame("non_trading_days", non_trading_days_df)

    def save_securities(self, securities_df: pd.DataFrame) -> None:
        self._insert_frame("securities", securities_df)

    def save_currencies(self, currencies_df: pd.DataFrame) -> None:
        self._insert_frame("currencies", currencies_df)

    def save_currency_pairs(self, currency_pairs_df: pd.DataFrame) -> None:
        self._insert_frame("currency_pairs", currency_pairs_df)

    def save_prices(self, prices_df: pd.DataFrame) -> None:
        self._insert_frame("prices", prices_df)

    def save_fx_rates(self, fx_rates_df: pd.DataFrame) -> None:
        """Write daily FX dates through explicit Snowflake text-to-timestamp conversion.

        Avoid Parquet/Pandas timestamp inference used by write_pandas for FX dates.
        This remains append-only; existing malformed rows are not altered.
        """
        if fx_rates_df.empty:
            return
        fields = WRITE_COLUMNS["fx_rates"]
        absent = set(fields) - set(fx_rates_df.columns)
        if absent:
            raise ValueError(f"fx_rates: missing columns {sorted(absent)}")

        prepared = fx_rates_df[fields].copy()
        dates = pd.to_datetime(prepared["date"], errors="raise")
        if dates.isna().any():
            raise ValueError("fx_rates: date cannot be null")
        prepared["date"] = dates.dt.strftime("%Y-%m-%d")
        prepared = prepared.astype(object).where(pd.notna(prepared), None)
        rows = list(prepared.itertuples(index=False, name=None))
        sql = (
            'INSERT INTO "FX_RATES" '
            '("CURRENCY_PAIR_ID", "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME") '
            "VALUES (%s, %s, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD'), %s, %s, %s, %s, %s)"
        )
        with self.connection.cursor() as cur:
            try:
                cur.execute("BEGIN")
                cur.executemany(sql, rows)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def get_calendar(self, calendar_id: int) -> pd.DataFrame:
        return self._query('SELECT * FROM "CALENDARS" WHERE "CALENDAR_ID" = %s', (calendar_id,))

    def get_trading_days(self, calendar_id: int, year: int) -> pd.DataFrame:
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        return self._query('SELECT * FROM "TRADING_DAYS" WHERE "CALENDAR_ID" = %s '
                           'AND "DATE" >= %s AND "DATE" < %s ORDER BY "DATE"',
                           (calendar_id, start, end))

    def get_securities(self, venue: str | None = None) -> pd.DataFrame:
        if venue is None:
            return self._query('SELECT * FROM "SECURITIES"')
        return self._query('SELECT * FROM "SECURITIES" WHERE "VENUE" = %s', (venue,))

    def get_currencies(self) -> pd.DataFrame:
        return self._query('SELECT * FROM "CURRENCIES"')

    def get_currency_pairs(self) -> pd.DataFrame:
        return self._query('SELECT * FROM "CURRENCY_PAIRS" WHERE "IS_ACTIVE" = %s', (True,))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
