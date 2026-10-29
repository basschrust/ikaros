"""Validated exchange index OHLC snapshots for Snowflake ingestion."""

from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

FIELDS = ("index_symbol", "date", "open", "high", "low", "close", "source")


def read_index_csv(path: str | Path) -> list[tuple]:
    rows = []
    seen = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or any(f not in reader.fieldnames for f in FIELDS):
            raise ValueError(f"CSV must contain: {', '.join(FIELDS)}")
        for item in reader:
            symbol = item["index_symbol"].strip().upper()
            if not symbol or not symbol.replace("_", "").isalnum():
                raise ValueError(f"Invalid index symbol {symbol!r}")
            trading_date = date.fromisoformat(item["date"].strip())
            prices = [float(item[k]) for k in ("open", "high", "low", "close")]
            op, high, low, close = prices
            if not all(math.isfinite(x) and x > 0 for x in prices):
                raise ValueError(f"Invalid index price on {trading_date}")
            if low > high or not (low <= op <= high and low <= close <= high):
                raise ValueError(f"Inconsistent OHLC for {symbol} on {trading_date}")
            source = item["source"].strip()
            if not source:
                raise ValueError("Source required")
            key = (symbol, trading_date)
            if key in seen:
                raise ValueError(f"Duplicate index observation: {key}")
            seen.add(key)
            rows.append((symbol, trading_date.isoformat(), *prices, source))
    if not rows:
        raise ValueError("CSV has no observations")
    return sorted(rows, key=lambda r: (r[0], r[1]))
