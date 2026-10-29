"""Candidate GPW derivatives listing parser. Discovery is not contract registration."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import json
import re

from bs4 import BeautifulSoup

SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{3,24}$")
ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class Candidate:
    symbol: str
    instrument_type: str
    expiry_date: str | None
    source: str
    source_as_of: str | None = None

    def encode(self) -> bytes:
        return json.dumps({"version": 1, **asdict(self)}, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, payload: bytes) -> "Candidate":
        obj = json.loads(payload)
        if obj.pop("version", None) != 1:
            raise ValueError("Unsupported candidate schema version")
        candidate = cls(**obj)
        if not SYMBOL.fullmatch(candidate.symbol) or candidate.instrument_type not in {
            "FUTURE",
            "OPTION",
        }:
            raise ValueError("Invalid candidate")
        if candidate.expiry_date is not None:
            date.fromisoformat(candidate.expiry_date)
        return candidate


def parse_bankier_futures(html: str, *, as_of: str | None = None) -> list[Candidate]:
    """Read explicit contract/expiry pairs from listing rows; never infer from ticker.

    Assumes a table has a Wygas. (expiry) header. Continuous 'KONT' is discarded.
    This is best-effort source discovery, not a complete exchange roster.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = {}
    for table in soup.find_all("table"):
        headers = [e.get_text(" ", strip=True).lower() for e in table.find_all("th")]
        expiry_columns = [i for i, h in enumerate(headers) if "wygas" in h]
        if not expiry_columns:
            continue
        expiry_idx = expiry_columns[-1]
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) <= expiry_idx:
                continue
            symbol_text = cells[0].get_text(" ", strip=True).upper().split()
            if not symbol_text:
                continue
            symbol = symbol_text[0]
            if not SYMBOL.fullmatch(symbol) or "KONT" in symbol:
                continue
            match = ISO_DATE.search(cells[expiry_idx].get_text(" ", strip=True))
            if not match:
                continue
            expiry = match.group(1)
            date.fromisoformat(expiry)
            candidates[symbol] = Candidate(symbol, "FUTURE", expiry, "bankier", as_of)
    return sorted(candidates.values(), key=lambda x: x.symbol)


def parse_money_options(html: str, *, as_of: str | None = None) -> list[Candidate]:
    """Collect option ticker candidates only; expiry/strike need verified reference data."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for cell in soup.select("td, a"):
        symbol = cell.get_text(" ", strip=True).upper()
        if re.fullmatch(r"OW20[A-Z0-9]{5,16}", symbol):
            found.add(symbol)
    return [Candidate(s, "OPTION", None, "money.pl", as_of) for s in sorted(found)]
