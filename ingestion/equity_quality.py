"""Conservative quality rules for equity EOD prices.

Zero-volume bars can be real for illiquid securities. Therefore we flag flat,
zero-volume candles for any venue, and all zero-volume candles only for an
explicit watchlist of liquid instruments. We never synthesize replacements.
"""

from __future__ import annotations

import pandas as pd

LIQUID_VOLUME_REQUIRED = frozenset({"PZU.WA"})
OHLC = ("open", "high", "low", "close")


def suspect_zero_volume(frame: pd.DataFrame, symbol: str) -> pd.Series:
    """Return boolean row mask; missing volume is not automatically zero."""
    if frame.empty:
        return pd.Series(False, index=frame.index, dtype=bool)
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    zero = volume.eq(0)
    if symbol in LIQUID_VOLUME_REQUIRED:
        return zero.fillna(False)
    prices = [pd.to_numeric(frame[col], errors="coerce") for col in OHLC]
    flat = prices[0].notna()
    for price in prices[1:]:
        flat = flat & price.eq(prices[0])
    return (zero & flat).fillna(False)


def validate_repair_candles(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Fail closed if any replacement candle is malformed, missing, or inactive."""
    required = {"symbol", "date", *OHLC, "volume"}
    missing = required - set(frame)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    result = frame.copy()
    if result.empty or not result["symbol"].eq(symbol).all():
        raise ValueError("Empty or different-symbol repair data")
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
    if result["date"].duplicated().any():
        raise ValueError("Duplicate repair dates")
    for col in (*OHLC, "volume"):
        result[col] = pd.to_numeric(result[col], errors="raise")
        if (
            result[col].isna().any()
            or not result[col].map(lambda x: pd.notna(x) and abs(x) != float("inf")).all()
        ):
            raise ValueError(f"Nonfinite or absent {col}")
    if (result[list(OHLC)] <= 0).any().any() or result["volume"].le(0).any():
        raise ValueError("Replacement requires positive OHLC and volume")
    if (
        (result["high"] < result[["open", "close", "low"]].max(axis=1))
        | (result["low"] > result[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError("Inconsistent OHLC range")
    return result
