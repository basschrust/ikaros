"""Discover existing WIG20 series from listing rows, never generate a strike grid."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

BANKIER = {
    "FUTURE": "https://www.bankier.pl/gielda/notowania/futures",
    "OPTION": "https://www.bankier.pl/gielda/notowania/opcje",
}
BIZNESRADAR = {
    "FUTURE": "https://www.biznesradar.pl/gielda/pochodne_kontrakty",
    "OPTION": "https://www.biznesradar.pl/gielda/pochodne_opcje",
}
STANDARDS = {
    "FUTURE": "https://bossa.pl/edukacja/kontrakty-opcje/kontrakty-terminowe/standardy",
    "OPTION": "https://www.equityresearch.eu/edukacja/kontrakty-opcje/opcje/standardy",
}
FUTURE = re.compile(r"FW20([HMUZ])(\d{2})20")
OPTION = re.compile(r"OW20([A-X])(\d{2})(\d{4})")
ISO_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class ListedContract:
    symbol: str
    kind: str
    expiry: date
    strike: int | None
    side: str | None
    sources: tuple[str, ...]
    reported_expiry: date | None

    def manifest_row(self) -> dict[str, str]:
        return {
            "contract_id": self.symbol,
            "symbol": self.symbol,
            "instrument_type": self.kind,
            "underlying_symbol": "WIG20",
            "expiry_date": self.expiry.isoformat(),
            "currency": "PLN",
            "multiplier": "20" if self.kind == "FUTURE" else "10",
            "strike": str(self.strike) if self.strike is not None else "",
            "option_side": self.side or "",
            "source": " | ".join((*self.sources, STANDARDS[self.kind], "STANDARD_DERIVED")),
        }


def listing_rows(*, html: str, kind: str, bankier: bool) -> dict[str, date | None]:
    """Only exact ticker cells in quote tables constitute listing evidence."""
    pattern = FUTURE if kind == "FUTURE" else OPTION
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, date | None] = {}
    for row in soup.select("table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        # First cell may contain an icon or a chart link in addition to the name.
        text = cells[0].get_text(" ", strip=True).upper()
        tokens = [s for s in text.split() if pattern.fullmatch(s)]
        if not tokens:
            continue
        if len(set(tokens)) != 1:
            raise ValueError(f"Ambiguous {kind} listing row: {text}")
        symbol = tokens[0]
        expiry = None
        if bankier:
            match = ISO_DATE.search(cells[-1].get_text(" ", strip=True))
            if match:
                expiry = date.fromisoformat(match.group())
        if symbol in found and found[symbol] != expiry:
            raise ValueError(f"Contradictory listing rows for {symbol}")
        found[symbol] = expiry
    if not found:
        raise ValueError(f"No exact WIG20 {kind} rows in listing; possible source change")
    return found


def contract_terms(
    *, symbol: str, closures: set[date], calendar_years: set[int]
) -> tuple[str, date, int | None, str | None]:
    """Decode the published standard; account for official exchange closures."""
    future = FUTURE.fullmatch(symbol)
    option = OPTION.fullmatch(symbol)
    if future:
        letter, year = future.groups()
        kind, month, strike, side = "FUTURE", {"H": 3, "M": 6, "U": 9, "Z": 12}[letter], None, None
    elif option:
        letter, year, raw_strike = option.groups()
        offset = ord(letter) - ord("A")
        kind, month = "OPTION", offset % 12 + 1
        strike, side = int(raw_strike), "CALL" if offset < 12 else "PUT"
        if strike <= 0:
            raise ValueError(f"Invalid option strike: {symbol}")
    else:
        raise ValueError(f"Unsupported WIG20 series: {symbol}")
    full_year = 2000 + int(year)
    if full_year not in calendar_years:
        raise ValueError(f"No maintained GPW calendar for expiry year {full_year}: {symbol}")
    first = date(full_year, month, 1)
    expiry = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    while expiry.weekday() >= 5 or expiry in closures:
        expiry -= timedelta(days=1)
    return kind, expiry, strike, side


def combine_listings(
    *,
    listings: dict[str, dict[str, date | None]],
    as_of: date,
    closures: set[date],
    calendar_years: set[int],
) -> tuple[list[ListedContract], list[str]]:
    contracts = []
    issues = []
    all_symbols = {symbol for entries in listings.values() for symbol in entries}
    for symbol in sorted(all_symbols):
        try:
            kind, expiry, strike, side = contract_terms(
                symbol=symbol, closures=closures, calendar_years=calendar_years
            )
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if expiry < as_of:
            continue
        sources = tuple(sorted(url for url, rows in listings.items() if symbol in rows))
        dates = {rows[symbol] for rows in listings.values() if rows.get(symbol) is not None}
        # Bankier has historically displayed one calendar day before expiry.
        # Accept only the standard date or that known display discrepancy.
        if len(dates) > 1 or any(d not in {expiry, expiry - timedelta(days=1)} for d in dates):
            issues.append(
                f"{symbol}: listing expiry {sorted(dates)} differs from standard {expiry}"
            )
            continue
        contracts.append(
            ListedContract(symbol, kind, expiry, strike, side, sources, next(iter(dates), None))
        )
    return contracts, issues


def discover(
    *, session: Any, as_of: date, closures: set[date], calendar_years: set[int]
) -> tuple[list[ListedContract], list[str]]:
    """Union independent listings. A failed source is visible, not an empty market."""
    listings = {}
    issues = []
    for bankier, urls in ((False, BIZNESRADAR), (True, BANKIER)):
        for kind, url in urls.items():
            try:
                response = session.get(
                    url, timeout=30, headers={"User-Agent": "Ikaros/0.4", "Accept": "text/html"}
                )
                response.raise_for_status()
                rows = listing_rows(html=response.text, kind=kind, bankier=bankier)
                listings[url] = rows
                print(f"LISTING {url}: {len(rows)} exact WIG20 symbols", flush=True)
            except (OSError, ValueError, RuntimeError) as exc:
                issues.append(f"{url}: {exc}")
    contracts, conflicts = combine_listings(
        listings=listings, as_of=as_of, closures=closures, calendar_years=calendar_years
    )
    return contracts, issues + conflicts
