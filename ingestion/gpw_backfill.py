"""Pure helpers for GPW daily-equity reference data and OHLCV backfills."""

from __future__ import annotations

from datetime import date
import logging

import pandas as pd

LOG = logging.getLogger(__name__)

PRICE_FIELDS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "dividend",
    "split_ratio",
)


def year_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive calendar-year windows; end may not precede start."""
    if end < start:
        raise ValueError("end must not precede start")
    return [
        (max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))
        for year in range(start.year, end.year + 1)
    ]


def normalize_securities(frame: pd.DataFrame) -> pd.DataFrame:
    """Standardize Ikaros data/gpw_stocks.csv without minting IDs prematurely."""
    if not {"ticker", "name"} <= set(frame.columns):
        raise ValueError("Expected 'ticker' and 'name' columns")
    result = frame[["ticker", "name"]].copy()
    result = result.dropna(subset=["ticker", "name"])
    result["ticker"] = result["ticker"].astype(str).str.strip().str.upper()
    result["name"] = result["name"].astype(str).str.strip()
    result = result[(result["ticker"] != "") & (result["name"] != "")]
    result["symbol"] = result["ticker"].map(lambda s: s if s.endswith(".WA") else f"{s}.WA")
    if result["symbol"].duplicated().any():
        duplicates = result.loc[result["symbol"].duplicated(), "symbol"].tolist()
        raise ValueError(f"Duplicate GPW symbols: {duplicates}")
    return result[["symbol", "name"]].reset_index(drop=True)


def normalize_prices(
    frame: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
    *,
    skip_invalid: bool = False,
) -> pd.DataFrame:
    """Normalize daily prices, optionally skipping invalid candles.

    Default remains strict for backfill diagnostics and existing callers.
    Routine updates can use ``skip_invalid=True`` to keep valid sessions
    when the provider returns a null, nonpositive or non-finite close.
    """
    if frame.empty:
        return pd.DataFrame(columns=list(PRICE_FIELDS))
    missing = set(PRICE_FIELDS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing price fields: {sorted(missing)}")
    result = frame[list(PRICE_FIELDS)].copy()
    if not result["symbol"].eq(symbol).all():
        raise ValueError("Provider returned prices for a different symbol")
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
    result = result.loc[result["date"].between(start, end)].copy()
    result = result.drop_duplicates(subset=["symbol", "date"], keep="last")
    for col in ["open", "high", "low", "close", "volume", "dividend", "split_ratio"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    bad_close = result["close"].isna() | result["close"].le(0)
    bad_volume = result["volume"].lt(0)
    # +/- infinity is not a valid price or volume, even though it isn't NaN.
    for col in ("open", "high", "low", "close", "volume", "dividend", "split_ratio"):
        bad_numeric = result[col].isin([float("inf"), float("-inf")])
        if col == "close":
            bad_close = bad_close | bad_numeric
        else:
            bad_volume = bad_volume | bad_numeric

    if not skip_invalid:
        if bad_close.any():
            raise ValueError("Closing prices must be positive and non-null")
        if bad_volume.any():
            raise ValueError("Volume cannot be negative or non-finite")
    else:
        invalid = bad_close | bad_volume
        for record in result.loc[invalid, ["date"]].itertuples(index=False):
            LOG.warning("SKIP INVALID GPW CANDLE %s %s", symbol, record.date)
        result = result.loc[~invalid].copy()

    return result.sort_values("date").reset_index(drop=True)


def missing_observations(frame: pd.DataFrame, existing: set[date]) -> pd.DataFrame:
    return frame.loc[~frame["date"].isin(existing)].copy()
