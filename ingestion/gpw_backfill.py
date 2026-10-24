"""Pure helpers for GPW daily-equity reference data and OHLCV backfills."""

from __future__ import annotations

from datetime import date

import pandas as pd

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


def normalize_prices(frame: pd.DataFrame, symbol: str, start: date, end: date) -> pd.DataFrame:
    """Prepare data, reject bad values, return one observation per symbol/date."""
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
    if result["close"].isna().any() or result["close"].le(0).any():
        raise ValueError("Closing prices must be positive and non-null")
    if result["volume"].lt(0).any():
        raise ValueError("Volume cannot be negative")
    return result.sort_values("date").reset_index(drop=True)


def missing_observations(frame: pd.DataFrame, existing: set[date]) -> pd.DataFrame:
    return frame.loc[~frame["date"].isin(existing)].copy()
