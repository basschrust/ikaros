"""Offline BiznesRadar historical-quotation HTML adapter.

Reads a user-supplied HTML snapshot, not a remote website. Uses only Python
stdlib for parsing; requires pandas, already installed in Ikaros.
"""

from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from pathlib import Path
import re

import pandas as pd

HEADINGS = {
    "data": "date",
    "otwarcie": "open",
    "max": "high",
    "min": "low",
    "zamknięcie": "close",
    "zamkniecie": "close",
    "wolumen": "volume",
}
FIELDS = ("date", "open", "high", "low", "close", "volume")


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.depth = 0
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self.depth == 0:
                self.table = []
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("th", "td") and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0 and self.table is not None:
                self.tables.append(self.table)
                self.table = None


def _number(value: str, *, integer: bool = False) -> float | int:
    s = re.sub(r"[\s\u00a0\u202f]", "", value).replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        raise ValueError(f"Invalid numeric value {value!r}")
    number = float(s)
    if integer:
        if not number.is_integer() or number < 0:
            raise ValueError(f"Invalid volume {value!r}")
        return int(number)
    return number


def parse_history_html(html: str, *, symbol: str, start: date, end: date) -> pd.DataFrame:
    """Parse a complete historical table without inferring a contract from the page.

    The caller is responsible for checking the saved page's symbol matches
    `symbol`, because third-party HTML structure and metadata can change.
    """
    if start > end:
        raise ValueError("start must be <= end")
    parser = _Tables()
    parser.feed(html)
    matched: list[list[list[str]]] = []
    for table in parser.tables:
        for i, row in enumerate(table):
            mapping = {HEADINGS.get(cell.lower()): index for index, cell in enumerate(row)}
            if all(field in mapping for field in FIELDS):
                matched.append([row, *table[i + 1 :]])
                break
    if len(matched) != 1:
        raise ValueError(f"Expected exactly one historical OHLCV table; found {len(matched)}")
    rows = matched[0]
    positions = {HEADINGS.get(cell.lower()): i for i, cell in enumerate(rows[0])}
    records = []
    for row in rows[1:]:
        if len(row) <= max(positions[field] for field in FIELDS):
            continue
        raw_day = row[positions["date"]]
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw_day):
            continue
        day = pd.to_datetime(raw_day, format="%d.%m.%Y", errors="raise").date()
        if not start <= day <= end:
            continue
        record = {"symbol": symbol, "date": day}
        for field in ("open", "high", "low", "close", "volume"):
            record[field] = _number(row[positions[field]], integer=field == "volume")
        records.append(record)
    if not records:
        raise ValueError("No historical rows in requested range (or page only contains one page)")
    frame = pd.DataFrame(records)
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate daily observations in HTML snapshot")
    if (frame["high"] < frame["low"]).any():
        raise ValueError("High below low")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Nonpositive OHLC")
    return frame.sort_values("date").reset_index(drop=True)


class BiznesRadarProvider:
    """Offline adapter for historical HTML snapshots of individual contracts.

    Snapshot filenames must be `<contract_symbol>.html`; `get_contracts()`
    deliberately remains unsupported until authoritative metadata is provided.
    """

    def __init__(self, snapshot_dir: str | Path):
        self.snapshot_dir = Path(snapshot_dir)

    def get_contracts(self) -> pd.DataFrame:
        raise NotImplementedError("Load verified contract specifications from a CSV manifest")

    def get_history(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        if not re.fullmatch(r"[A-Z0-9]+", symbol):
            raise ValueError("Invalid contract symbol")
        path = self.snapshot_dir / f"{symbol}.html"
        return parse_history_html(
            path.read_text(encoding="utf-8"), symbol=symbol, start=start_date, end=end_date
        )
