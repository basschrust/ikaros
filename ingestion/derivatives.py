"""Validate contract-level daily quotes; no artificial continuous-future stitching."""

from __future__ import annotations

import pandas as pd

TYPES = {"FUTURE", "OPTION"}
SIDES = {"CALL", "PUT"}
CONTRACT_COLUMNS = [
    "contract_id",
    "symbol",
    "instrument_type",
    "underlying_symbol",
    "expiry_date",
    "currency",
    "multiplier",
    "strike",
    "option_side",
    "source",
]
PRICE_COLUMNS = [
    "contract_id",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "settlement",
    "source",
]


def validate_contracts(frame: pd.DataFrame) -> pd.DataFrame:
    absent = set(CONTRACT_COLUMNS) - set(frame)
    if absent:
        raise ValueError(f"Missing contract columns: {sorted(absent)}")
    df = frame[CONTRACT_COLUMNS].copy()
    for col in (
        "contract_id",
        "symbol",
        "instrument_type",
        "underlying_symbol",
        "currency",
        "source",
    ):
        if df[col].isna().any() or df[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"Empty {col}")
        df[col] = df[col].astype(str).str.strip()
    df["instrument_type"] = df["instrument_type"].str.upper()
    if not df["instrument_type"].isin(TYPES).all():
        raise ValueError("Unknown instrument type")
    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="raise").dt.date
    for col in ("multiplier", "strike"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df["multiplier"].isna().any() or df["multiplier"].le(0).any():
        raise ValueError("Multiplier must be positive")
    is_option = df["instrument_type"].eq("OPTION")
    side = df["option_side"].astype("string").str.strip().str.upper()
    if (is_option & (~side.isin(SIDES).fillna(False))).any():
        raise ValueError("Options require CALL/PUT")
    if (is_option & (~df["strike"].gt(0))).any():
        raise ValueError("Options require positive strike")
    if ((~is_option) & (df["strike"].notna() | side.notna() & side.ne(""))).any():
        raise ValueError("Futures must not have strike/option_side")
    df["option_side"] = side.where(is_option, None)
    if df["contract_id"].duplicated().any() or df["symbol"].duplicated().any():
        raise ValueError("Duplicate contract ID or symbol")
    return df


def validate_prices(frame: pd.DataFrame, valid_contract_ids: set[str]) -> pd.DataFrame:
    absent = set(PRICE_COLUMNS) - set(frame)
    if absent:
        raise ValueError(f"Missing price columns: {sorted(absent)}")
    df = frame[PRICE_COLUMNS].copy()
    df["contract_id"] = df["contract_id"].astype(str).str.strip()
    if not set(df["contract_id"]) <= valid_contract_ids:
        raise ValueError("Unknown contract_id; seed reference data first")
    if df["source"].isna().any() or df["source"].astype(str).str.strip().eq("").any():
        raise ValueError("Missing source")
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.date
    for col in ("open", "high", "low", "close", "volume", "open_interest", "settlement"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].dropna().map(lambda v: not __import__("math").isfinite(float(v))).any():
            raise ValueError(f"Nonfinite {col}")
    if df["close"].isna().any() or df["close"].le(0).any():
        raise ValueError("Close must be positive")
    if (
        df["open"].dropna().le(0).any()
        or df["high"].dropna().le(0).any()
        or df["low"].dropna().le(0).any()
    ):
        raise ValueError("OHLC must be positive")
    if df["volume"].dropna().lt(0).any() or df["open_interest"].dropna().lt(0).any():
        raise ValueError("Negative volume/open interest")
    if (df["high"].notna() & df["low"].notna() & (df["high"] < df["low"])).any():
        raise ValueError("High below low")
    if df.duplicated(["contract_id", "date"]).any():
        raise ValueError("Duplicate contract/date observations")
    return df.sort_values(["contract_id", "date"]).reset_index(drop=True)
