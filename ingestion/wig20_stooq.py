"""Parse Stooq WIG20 CSV. All persistence and overlap SQL lives in database/."""

from __future__ import annotations

from datetime import date
from io import StringIO
from typing import Any
import math

import pandas as pd

from database import index_queries


def check_overlaps(conn: Any, frame: pd.DataFrame, *, tolerance: float = 0.5) -> tuple[int, list]:
    """Compatibility entry point; all SQL remains under database/."""
    return index_queries.check_index_price_overlaps(conn, frame, tolerance=tolerance)


def insert_missing(conn: Any, frame: pd.DataFrame, *, batch_size: int = 500) -> int:
    """Compatibility entry point; all SQL remains under database/."""
    return index_queries.insert_missing_index_prices(conn, frame, batch_size=batch_size)


SOURCE = "stooq|wig20|daily"
COLUMNS = ["date", "open", "high", "low", "close"]
ALIASES = {
    "date": "date",
    "data": "date",
    "open": "open",
    "otwarcie": "open",
    "high": "high",
    "najwyzszy": "high",
    "najwyższy": "high",
    "max": "high",
    "low": "low",
    "najnizszy": "low",
    "najniższy": "low",
    "min": "low",
    "close": "close",
    "zamkniecie": "close",
    "zamknięcie": "close",
}


def parse_csv(content: str, *, start: date, end: date) -> pd.DataFrame:
    """Accept Stooq English/Polish CSV headers; reject malformed daily OHLC."""
    if start > end:
        raise ValueError("start must be <= end")
    if not content.strip() or "no data" in content[:120].lower():
        raise ValueError("Provider returned no CSV observations")
    frame = pd.read_csv(StringIO(content.lstrip("\ufeff")), encoding="utf-8-sig")
    frame = frame.rename(columns={c: ALIASES.get(str(c).strip().lower(), c) for c in frame.columns})
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Stooq data missing columns: {sorted(missing)}")
    frame = frame[COLUMNS].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    frame = frame.loc[frame["date"].between(start, end)].copy()
    for col in COLUMNS[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if frame.empty:
        raise ValueError("No WIG20 observations inside requested dates")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate dates in provider response")
    for row in frame.itertuples(index=False):
        nums = (row.open, row.high, row.low, row.close)
        if not all(math.isfinite(float(n)) and n > 0 for n in nums):
            raise ValueError(f"Invalid prices on {row.date}")
        if (
            row.low > min(row.open, row.close)
            or row.high < max(row.open, row.close)
            or row.high < row.low
        ):
            raise ValueError(f"Inconsistent OHLC on {row.date}")
    return frame.sort_values("date").reset_index(drop=True)


__all__ = ["SOURCE", "COLUMNS", "ALIASES", "parse_csv", "check_overlaps", "insert_missing"]
