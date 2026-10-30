"""Backfill all BiznesRadar historical pages for registered FW20 futures.

Dry-run by default. --write MERGEs source-backed OHLCV into DERIVATIVE_PRICES.
Uses the existing project parser and persistence logic. No prices are synthesized.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

from ingestion.fw20_history import CONTRACT, parse_history
from run.refresh_fw20_prices import persist, registered

LOG = logging.getLogger(__name__)
BASE = "https://www.biznesradar.pl/notowania-historyczne/"
# The page-number suffix is documented by the site's historical navigation.
PAGE_COUNT = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)


def history_url(symbol: str, page: int) -> str:
    if not CONTRACT.fullmatch(symbol) or page < 1:
        raise ValueError("Invalid FW20 contract/page")
    return BASE + symbol + (f",{page}" if page > 1 else "")


def fetch_page(session, symbol: str, page: int, *, timeout: int) -> str:
    url = history_url(symbol, page)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.text.strip():
        raise RuntimeError(f"Empty response: {url}")
    return response.text


def advertised_pages(html: str, symbol: str) -> set[int]:
    # HTML links may be URL-encoded; avoid mistakenly following other instruments.
    from html import unescape
    from urllib.parse import unquote, urlsplit

    pages = set()
    for match in PAGE_COUNT.finditer(unescape(html)):
        href = unquote(match.group(1))
        segment = urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1]
        parsed = re.fullmatch(re.escape(symbol) + r",(\d+)", segment, re.I)
        if parsed:
            pages.add(int(parsed.group(1)))
    return pages


def gather_history(
    session,
    symbol: str,
    *,
    through: date,
    timeout: int = 25,
    delay: float = 1.0,
    max_pages: int = 100,
    html_dir: Path | None = None,
) -> tuple[list[dict], int, str]:
    """Read pages until end; reject repeated pages or missing advertised pages.

    The first page may be a partial list. A subsequent page with no OHLCV rows
    marks the end *only if* no further pages were advertised. The returned
    completion label is conservative; it means source pagination exhausted,
    not verified first exchange listing date.
    """
    all_rows: dict[date, dict] = {}
    previous_oldest = None
    visited = 0
    for page in range(1, max_pages + 1):
        if html_dir is None:
            html = fetch_page(session, symbol, page, timeout=timeout)
        else:
            path = html_dir / f"{symbol}{'' if page == 1 else f',{page}'}.html"
            if not path.exists():
                if page == 1:
                    raise FileNotFoundError(path)
                return (
                    sorted(all_rows.values(), key=lambda r: r["date"]),
                    visited,
                    "OFFLINE_PAGE_FILES_EXHAUSTED_UNVERIFIED",
                )
            html = path.read_text(encoding="utf-8")
        visited += 1
        rows = parse_history(html, symbol=symbol, through=through)
        links = advertised_pages(html, symbol)
        if not rows:
            if page == 1:
                raise RuntimeError(f"No historical data for {symbol}")
            if any(n > page for n in links):
                raise RuntimeError(f"Page {page} empty but advertises later pages for {symbol}")
            return sorted(all_rows.values(), key=lambda r: r["date"]), visited, "SOURCE_EXHAUSTED"
        oldest = min(r["date"] for r in rows)
        if previous_oldest is not None and oldest >= previous_oldest:
            raise RuntimeError(f"Page {page} did not advance into older history: {symbol}")
        previous_oldest = oldest
        for row in rows:
            day = row["date"]
            if day in all_rows:
                old = all_rows[day]
                if any(old[f] != row[f] for f in ("open", "high", "low", "close", "volume")):
                    raise RuntimeError(f"Conflicting overlap {symbol} on {day}")
            else:
                all_rows[day] = row
        LOG.info(
            "%s page=%d rows=%d total_unique=%d oldest=%s",
            symbol,
            page,
            len(rows),
            len(all_rows),
            oldest,
        )
        # When the source explicitly declares page 2, fetch it. Conversely if
        # no pagination is advertised, don't blindly treat that as proof of
        # history completeness: a site may truncate navigation or render it in JS.
        if not any(n > page for n in links):
            if len(rows) >= 50:
                raise RuntimeError(
                    f"Full page {page} but no next-page link for {symbol}; pagination unverified"
                )
            # Page 2+ may show only a back link; that is a valid terminal page.
            return (
                sorted(all_rows.values(), key=lambda r: r["date"]),
                visited,
                ("SOURCE_NAVIGATION_EXHAUSTED" if page > 1 else "SINGLE_PAGE_UNVERIFIED"),
            )
        if html_dir is None and delay:
            time.sleep(delay)
    raise RuntimeError(f"Reached max_pages={max_pages} for {symbol}; refusing incomplete backfill")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", help="Default: registered FW20 futures")
    parser.add_argument(
        "--through", type=date.fromisoformat, default=date.today() - timedelta(days=1)
    )
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument(
        "--html-dir", type=Path, help="Offline files SYMBOL.html, SYMBOL,2.html etc."
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.max_pages < 1 or args.timeout < 1 or args.delay < 0:
        parser.error("max-pages and timeout must be positive; delay nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import requests
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    failures = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; IkarosMarketResearch/1.0)"})
        with repository_from_config(load_config("database.yaml")) as repo:
            symbols = args.symbols or registered(repo.connection)
            if not symbols:
                parser.error("No FW20 contracts registered")
            for symbol in dict.fromkeys(symbols):
                if not CONTRACT.fullmatch(symbol):
                    parser.error(f"Unsupported contract: {symbol}")
                try:
                    rows, pages, status = gather_history(
                        session,
                        symbol,
                        through=args.through,
                        max_pages=args.max_pages,
                        timeout=args.timeout,
                        delay=args.delay,
                        html_dir=args.html_dir,
                    )
                    if not rows:
                        raise RuntimeError("No rows in historical source")
                    LOG.info(
                        "RESULT %s pages=%d rows=%d first=%s last=%s status=%s write=%s",
                        symbol,
                        pages,
                        len(rows),
                        rows[0]["date"],
                        rows[-1]["date"],
                        status,
                        args.write,
                    )
                    if args.write and status in {
                        "SOURCE_NAVIGATION_EXHAUSTED",
                        "SINGLE_PAGE_UNVERIFIED",
                    }:
                        persist(repo.connection, rows)
                    elif args.write:
                        raise RuntimeError(
                            f"Refusing write with unverified page coverage: {status}"
                        )
                except Exception:
                    LOG.exception("FAILED %s", symbol)
                    failures.append(symbol)
                if args.html_dir is None and args.delay:
                    time.sleep(args.delay)
    if failures:
        raise SystemExit(f"Failed contracts: {', '.join(failures)}")


if __name__ == "__main__":
    main()
