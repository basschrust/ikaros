"""Parse independently published daily OHLCV for individual WIG20 futures.

Only completed historical rows are accepted. No synthetic front-month prices,
settlement prices, or open interest are inferred.
"""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
import math
import re

CONTRACT = re.compile(r"^FW20[HMUZ][0-9]{2}20$")
DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.depth = 0
        self.rows = None
        self.cells = None
        self.cell_depth = 0
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.rows = []
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.cells = []
        elif self.depth == 1 and tag in ("td", "th") and self.cells is not None:
            self.current = []
            self.cell_depth = 1
        elif self.current is not None:
            self.cell_depth += 1

    def handle_endtag(self, tag):
        if self.current is not None:
            if tag in ("td", "th") and self.cell_depth == 1:
                self.cells.append(" ".join("".join(self.current).split()))
                self.current = None
                self.cell_depth = 0
            elif self.cell_depth > 1:
                self.cell_depth -= 1
        if tag == "tr" and self.depth == 1 and self.cells is not None:
            if self.cells:
                self.rows.append(self.cells)
            self.cells = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.tables.append(self.rows)
                self.rows = None

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)


def _number(value: str) -> float:
    cleaned = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    number = float(cleaned)
    if not math.isfinite(number):
        raise ValueError("Non-finite price or volume")
    return number


def parse_history(html: str, *, symbol: str, through: date) -> list[dict]:
    """Return ascending, validated, source-backed history from BiznesRadar HTML."""
    if not CONTRACT.fullmatch(symbol):
        raise ValueError(f"Unsupported contract: {symbol}")
    tables = _Tables()
    tables.feed(html)
    found = []
    for table in tables.tables:
        # Require specific historical OHLCV columns, not a live quote table.
        header_idx = next(
            (
                i
                for i, row in enumerate(table)
                if len(row) >= 6
                and row[0].strip().lower() == "data"
                and "otwarcie" in row[1].lower()
                and "zamkni" in row[4].lower()
                and "wolumen" in row[5].lower()
            ),
            None,
        )
        if header_idx is None:
            continue
        for row in table[header_idx + 1 :]:
            if len(row) < 6 or not DATE.fullmatch(row[0]):
                continue
            day = datetime.strptime(row[0], "%d.%m.%Y").date()
            if day > through:
                continue
            op, hi, lo, close, vol = [_number(v) for v in row[1:6]]
            if min(op, hi, lo, close) <= 0 or vol < 0:
                raise ValueError(f"Invalid OHLCV for {symbol} {day}")
            if hi < max(op, lo, close) or lo > min(op, hi, close):
                raise ValueError(f"Inconsistent OHLC for {symbol} {day}")
            found.append(
                dict(
                    contract_id=symbol,
                    date=day,
                    open=op,
                    high=hi,
                    low=lo,
                    close=close,
                    volume=vol,
                    open_interest=None,
                    settlement=None,
                    source="biznesradar|html_history",
                )
            )
    dates = [r["date"] for r in found]
    if len(set(dates)) != len(dates):
        raise ValueError(f"Duplicate dates for {symbol}")
    return sorted(found, key=lambda r: r["date"])
