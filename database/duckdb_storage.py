"""DuckDB Ikaros repository; shared pre-1.0 schema authority in database.schema."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from database.duckdb_file_lock import DuckDBAccessTimeout, duckdb_access_lock
from database.schema import prepare_tables as create_all_tables


class Repository:
    def __init__(self, output_dir: Path | str = "output") -> None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.localdb = output_dir / "ikaros.duckdb"
        # The mutex must precede duckdb.connect and remain held until close.
        self._access_context = duckdb_access_lock(database_path=self.localdb)
        try:
            self._access_context.__enter__()
        except DuckDBAccessTimeout as exc:
            # The shared CLI error handler already formats duckdb.IOException.
            raise duckdb.IOException(str(exc)) from exc
        try:
            self.connection = duckdb.connect(str(self.localdb))
            self.prepare_tables()
        except BaseException:
            try:
                if hasattr(self, "connection"):
                    self.connection.close()
            finally:
                self._release_access_lock()
            raise

    def _release_access_lock(self) -> None:
        context = self._access_context
        self._access_context = None
        if context is not None:
            context.__exit__(None, None, None)

    def prepare_tables(self) -> None:
        create_all_tables(self.connection, backend="duckdb")

    def _save(self, name: str, frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
        if frame.empty:
            return
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: missing {sorted(missing)}")
        self.connection.register("_ikaros_frame", frame)
        try:
            cols = ", ".join('"' + c + '"' for c in columns)
            self.connection.execute(
                f'INSERT INTO "{name}" ({cols}) SELECT {cols} FROM _ikaros_frame'
            )
        finally:
            self.connection.unregister("_ikaros_frame")

    def save_calendars(self, calendars_df: pd.DataFrame) -> None:
        self._save(
            "calendars",
            calendars_df,
            ("calendar_id", "name", "days_per_year", "init_date", "working_days", "comment"),
        )

    def save_trading_days(self, trading_days_df: pd.DataFrame) -> None:
        self._save(
            "trading_days",
            trading_days_df,
            ("date", "calendar_id", "comment", "load_status", "load_date"),
        )

    def save_non_trading_days(self, non_trading_days_df: pd.DataFrame) -> None:
        self._save("non_trading_days", non_trading_days_df, ("date", "calendar_id", "comment"))

    def save_securities(self, securities_df: pd.DataFrame) -> None:
        self._save(
            "securities",
            securities_df,
            (
                "asset_id",
                "instrument_class",
                "market_type",
                "name",
                "symbol",
                "currency",
                "calendar_id",
                "init_date",
                "venue",
            ),
        )

    def save_currencies(self, currencies_df: pd.DataFrame) -> None:
        self._save(
            "currencies",
            currencies_df,
            (
                "currency_id",
                "symbol",
                "calendar_id",
                "name",
                "comment",
                "code",
                "minor_unit",
                "is_active",
            ),
        )

    def save_currency_pairs(self, currency_pairs_df: pd.DataFrame) -> None:
        self._save(
            "currency_pairs",
            currency_pairs_df,
            ("currency_pair_id", "base_currency_id", "quote_currency_id", "symbol", "is_active"),
        )

    def save_prices(self, prices_df: pd.DataFrame) -> None:
        self._save(
            "prices",
            prices_df,
            ("symbol", "date", "open", "high", "low", "close", "volume", "dividend", "split_ratio"),
        )

    def save_fx_rates(self, fx_rates_df: pd.DataFrame) -> None:
        self._save(
            "fx_rates",
            fx_rates_df,
            ("currency_pair_id", "symbol", "date", "open", "high", "low", "close", "volume"),
        )

    def get_calendar(self, calendar_id: int) -> pd.DataFrame:
        return self.connection.execute(
            "SELECT calendar_id,name,days_per_year,init_date,working_days,comment FROM calendars WHERE calendar_id=?",
            [calendar_id],
        ).fetchdf()

    def get_trading_days(self, calendar_id: int, year: int) -> pd.DataFrame:
        return self.connection.execute(
            "SELECT date,calendar_id,comment,load_status,load_date FROM trading_days "
            "WHERE calendar_id=? AND date>=? AND date<? ORDER BY date",
            [calendar_id, f"{year}-01-01", f"{year + 1}-01-01"],
        ).fetchdf()

    def get_securities(self, venue: str | None = None) -> pd.DataFrame:
        if venue is None:
            return self.connection.execute(
                "SELECT asset_id,instrument_class,market_type,name,symbol,currency,"
                "calendar_id,init_date,venue FROM securities"
            ).fetchdf()
        return self.connection.execute(
            "SELECT asset_id,instrument_class,market_type,name,symbol,currency,"
            "calendar_id,init_date,venue FROM securities WHERE venue=?",
            [venue],
        ).fetchdf()

    def get_currencies(self) -> pd.DataFrame:
        return self.connection.execute("SELECT * FROM currencies").fetchdf()

    def get_currency_pairs(self) -> pd.DataFrame:
        return self.connection.execute(
            "SELECT * FROM currency_pairs WHERE is_active=TRUE"
        ).fetchdf()

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._release_access_lock()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
