"""Build synthetic FX closing prices from date-aligned market observations.

Conventions: ABCXYZ=X quotes units of XYZ per one unit ABC.
For example, CADPLN = USDPLN / USDCAD.
Never infer OHLC extrema from daily source bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class CrossSpec:
    target: str
    numerator: str
    denominator: str


CROSSES: dict[str, CrossSpec] = {
    "CADPLN=X": CrossSpec("CADPLN=X", "USDPLN=X", "USDCAD=X"),
    "NOKPLN=X": CrossSpec("NOKPLN=X", "USDPLN=X", "USDNOK=X"),
    "SEKPLN=X": CrossSpec("SEKPLN=X", "USDPLN=X", "USDSEK=X"),
    "CNYPLN=X": CrossSpec("CNYPLN=X", "USDPLN=X", "USDCNY=X"),
    "CZKPLN=X": CrossSpec("CZKPLN=X", "EURPLN=X", "EURCZK=X"),
}


def derive_closes(
    numerator: pd.DataFrame,
    denominator: pd.DataFrame,
    spec: CrossSpec,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Use the intersection of available dates; reject bad and ambiguous prices.

    Source frames have 'date' and 'close'. Output: date, symbol, close,
    comment. Existing FX schema supports comment without migrations.
    """
    if start > end:
        raise ValueError("start must not be later than end")

    def clean(frame: pd.DataFrame, label: str) -> pd.DataFrame:
        required = {"date", "close"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{label}: missing columns {sorted(required - set(frame.columns))}")
        df = frame[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"], errors="raise").dt.date
        df = df.loc[df["date"].between(start, end)].copy()
        if df["date"].duplicated().any():
            raise ValueError(f"{label}: duplicate source dates (clean source data first)")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        if df["close"].isna().any() or (df["close"] <= 0).any():
            raise ValueError(f"{label}: missing or nonpositive close")
        return df

    top = clean(numerator, spec.numerator)
    bottom = clean(denominator, spec.denominator)
    aligned = top.merge(bottom, on="date", how="inner", suffixes=("_num", "_den"))
    aligned["close"] = aligned["close_num"] / aligned["close_den"]
    aligned["symbol"] = spec.target
    aligned["comment"] = f"DERIVED_CLOSE: {spec.numerator} / {spec.denominator}"
    return aligned[["date", "symbol", "close", "comment"]].sort_values("date").reset_index(drop=True)
