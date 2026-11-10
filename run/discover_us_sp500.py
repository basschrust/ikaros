"""Export a reviewable S&P 500 symbol snapshot to data/us_sp500.csv.

Source: public Wikipedia constituents table (convenience, not licensed point-in-time
index membership). The CSV must be reviewed before seeding securities.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def parse_constituents(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="constituents")
    if table is None:
        raise ValueError("S&P 500 constituents table missing; source layout changed")
    header_row = table.find("tr")
    if header_row is None:
        raise ValueError("Missing table headers")
    headings = [x.get_text(" ", strip=True).lower() for x in header_row.find_all(["th", "td"])]
    if "symbol" not in headings or "security" not in headings:
        raise ValueError(f"Unexpected constituents columns: {headings}")
    si, ni = headings.index("symbol"), headings.index("security")
    result = {}
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) <= max(si, ni):
            continue
        sym = cells[si].get_text(" ", strip=True).upper().replace(".", "-")
        name = cells[ni].get_text(" ", strip=True)
        if not sym or not name or sym in result:
            raise ValueError(f"Invalid or duplicate constituent: {sym}")
        result[sym] = name
    # Fail closed on partial HTML or accidental wrong table.
    if not 400 <= len(result) <= 550:
        raise ValueError(f"Unexpected S&P 500 symbols count: {len(result)}")
    return sorted(result.items())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path("data/us_sp500.csv"))
    p.add_argument("--html-file", type=Path, help="Use a saved Wikipedia page offline")
    args = p.parse_args()
    if args.html_file:
        html = args.html_file.read_text(encoding="utf-8")
    else:
        resp = requests.get(URL, headers={"User-Agent": "IkarosMarketResearch/1.0"}, timeout=30)
        resp.raise_for_status()
        html = resp.text
    rows = parse_constituents(html)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["ticker", "name"])
        writer.writerows(rows)
    print(f"Saved {len(rows)} S&P 500 share classes to {args.out}; review before seeding")


if __name__ == "__main__":
    main()
