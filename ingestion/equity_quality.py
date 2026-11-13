"""Conservative quality checks for equity daily prices and repairs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import pandas as pd

LIQUID_VOLUME_REQUIRED = frozenset({"PZU.WA"})
OHLC = ("open", "high", "low", "close")


def suspect_zero_volume(frame: pd.DataFrame, symbol: str) -> pd.Series:
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


def _strict_decimal(value: object, column: str) -> Decimal:
    if pd.isna(value):
        raise ValueError(f"Nonfinite or absent {column}")
    try:
        value_decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {column}") from exc
    if not value_decimal.is_finite():
        raise ValueError(f"Nonfinite or absent {column}")
    return value_decimal


def validate_repair_candles(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Validate source OHLCV using Decimal without a numeric float round-trip."""
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
    for column in (*OHLC, "volume"):
        result[column] = result[column].map(lambda item: _strict_decimal(item, column))
    for row in result.itertuples(index=False):
        if min(row.open, row.high, row.low, row.close, row.volume) <= 0:
            raise ValueError("Replacement requires positive OHLC and volume")
        if row.volume != row.volume.to_integral_value():
            raise ValueError("Replacement volume must be integral")
        if row.high < max(row.open, row.close, row.low) or row.low > min(
            row.open, row.close, row.high
        ):
            raise ValueError("Inconsistent OHLC range")
    return result
