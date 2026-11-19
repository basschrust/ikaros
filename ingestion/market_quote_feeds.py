"""Source adapters for WIG20 index and registered GPW contracts.

No invented contracts, no synthetic index turnover/volume and no settlement
or open interest inferred from other fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

import requests
from bs4 import BeautifulSoup

from ingestion.gpw_provider_feeds import RawCandle, _one, _unaccent


@dataclass(frozen=True)
class MarketCandle:
    day: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None = None
    settlement: Decimal | None = None
    open_interest: int | None = None


class MarketFeed(Protocol):
    code: str

    def history(self, symbol: str, start: date, end: date) -> list[MarketCandle]: ...


def _from_raw(item: RawCandle, *, volume: int | None = None) -> MarketCandle:
    return MarketCandle(item.day, item.open, item.high, item.low, item.close, volume)


def parse_index_history(html: str, symbol: str) -> list[MarketCandle]:
    """Parse OHLC index points; volume/turnover columns are not share volumes."""
    soup = BeautifulSoup(html, "html.parser")
    header_text = " ".join(tag.get_text(" ", strip=True) for tag in soup.find_all(("h1", "title")))
    label = _unaccent(header_text)
    if "notowania historyczne" not in label or not re.search(
        rf"(?<![a-z0-9]){re.escape(symbol.lower())}(?![a-z0-9])", label
    ):
        raise ValueError(f"BiznesRadar index page does not identify {symbol}")
    if any(
        word in _unaccent(soup.get_text(" ", strip=True))[:1200]
        for word in ("captcha", "access denied", "checking your browser")
    ):
        raise ValueError("BiznesRadar access challenge")
    aliases = {
        "date": {"data", "date"},
        "open": {"otwarcie", "open"},
        "high": {"max", "maks", "high"},
        "low": {"min", "low"},
        "close": {"zamkniecie", "close"},
    }
    found: dict[date, MarketCandle] = {}
    for table in soup.find_all("table"):
        header = table.find("thead") or table
        first = header.find("tr")
        if first is None:
            continue
        names = [
            _unaccent(x.get_text(" ", strip=True))
            for x in first.find_all(("td", "th"), recursive=False)
        ]
        indices: dict[str, int] = {}
        for field, possibilities in aliases.items():
            match = [i for i, col in enumerate(names) if col in possibilities]
            if len(match) != 1:
                break
            indices[field] = match[0]
        if len(indices) != len(aliases):
            continue
        for row in table.find_all("tr"):
            cells = [
                v.get_text(" ", strip=True) for v in row.find_all(("td", "th"), recursive=False)
            ]
            if len(cells) <= max(indices.values()):
                continue
            dt = cells[indices["date"]]
            if not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", dt):
                continue
            raw = _one(
                dt,
                cells[indices["open"]],
                cells[indices["high"]],
                cells[indices["low"]],
                cells[indices["close"]],
                None,
            )
            item = _from_raw(raw)
            if item.day in found and item != found[item.day]:
                raise ValueError(f"Conflicting index candle {item.day}")
            found[item.day] = item
    if not found:
        raise ValueError("BiznesRadar WIG20 OHLC history table missing/empty")
    return sorted(found.values(), key=lambda x: x.day)


@dataclass
class BiznesRadarIndexFeed:
    code: str = "BIZNESRADAR"
    session: requests.Session | None = None
    max_pages: int = 10

    def history(self, symbol: str, start: date, end: date) -> list[MarketCandle]:
        if symbol != "WIG20":
            raise ValueError("Index adapter currently supports only reviewed WIG20")
        client = self.session or requests
        found: dict[date, MarketCandle] = {}
        old = None
        for page in range(1, self.max_pages + 1):
            url = "https://www.biznesradar.pl/notowania-historyczne/WIG20"
            if page > 1:
                url += f",{page}"
            response = client.get(url, timeout=25, headers={"User-Agent": "Ikaros/0.4"})
            response.raise_for_status()
            rows = parse_index_history(response.text, symbol)
            for row in rows:
                if start <= row.day <= end:
                    found[row.day] = row
            oldest = min(row.day for row in rows)
            if oldest <= start:
                break
            if old is not None and oldest >= old:
                raise ValueError("Nonadvancing BiznesRadar index pagination")
            old = oldest
        else:
            raise ValueError("Index history exceeded max_pages")
        if not found:
            raise ValueError("No BiznesRadar WIG20 sessions in window")
        return sorted(found.values(), key=lambda x: x.day)


@dataclass
class YahooIndexFeed:
    code: str = "YFINANCE"

    def history(self, symbol: str, start: date, end: date) -> list[MarketCandle]:
        if symbol != "WIG20.WA":
            raise ValueError("Unreviewed Yahoo index symbol")
        import yfinance as yf
        from ingestion.index_prices_refresh import (
            aggregate_hourly_index_prices,
            normalize_yahoo_index_prices,
        )

        end_exclusive = (end + timedelta(days=1)).isoformat()
        raw = yf.download(
            symbol,
            start=start.isoformat(),
            end=end_exclusive,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        frame = normalize_yahoo_index_prices(raw, start=start, end=end)
        if frame.empty:
            hourly = yf.download(
                symbol,
                start=start.isoformat(),
                end=end_exclusive,
                interval="1h",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            frame = aggregate_hourly_index_prices(hourly, start=start, end=end)
        if frame.empty:
            raise ValueError("Yahoo returned no trustworthy completed WIG20 bars")
        return [
            MarketCandle(
                rec.date,
                *(
                    Decimal(str(getattr(rec, x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    for x in ("open", "high", "low", "close")
                ),
            )
            for rec in frame.itertuples(index=False)
        ]


@dataclass
class StooqIndexFeed:
    """Optional, authorized Stooq daily WIG20 CSV; no guessed volume semantics."""

    code: str = "STOOQ"

    def history(self, symbol: str, start: date, end: date) -> list[MarketCandle]:
        if symbol.lower() != "wig20":
            raise ValueError("Unreviewed Stooq index symbol")
        from ingestion.gpw_provider_feeds import StooqFeed

        # Stooq's raw CSV may contain a Volume column for indices, but it
        # isn't necessarily exchange-traded share volume. Ignore it here.
        rows = StooqFeed().history(symbol, start, end)
        return [_from_raw(row) for row in rows]


@dataclass
class BiznesRadarDerivativeFeed:
    code: str = "BIZNESRADAR"
    session: requests.Session | None = None
    max_pages: int = 50

    def history(self, symbol: str, start: date, end: date) -> list[MarketCandle]:
        from ikaros_app.derivative_prices import collect

        client = self.session or requests.Session()
        rows, coverage, _pages = collect(
            client, symbol, start, end, max_pages=self.max_pages, delay=0
        )
        if coverage == "UNVERIFIED_SOURCE_END":
            raise ValueError(f"Unverified derivative history pagination for {symbol}")
        if not rows:
            return []  # A thinly traded option may have no daily trades.
        return [
            MarketCandle(
                row["date"],
                *(
                    Decimal(str(row[k])) if row[k] is not None else None
                    for k in ("open", "high", "low", "close")
                ),
                int(row["volume"]) if row["volume"] is not None else None,
                Decimal(str(row["settlement"])) if row.get("settlement") is not None else None,
                int(row["open_interest"]) if row.get("open_interest") is not None else None,
            )
            for row in rows
        ]


def default_feeds(kind: str) -> dict[str, MarketFeed]:
    if kind == "INDEX":
        return {
            item.code: item for item in (BiznesRadarIndexFeed(), StooqIndexFeed(), YahooIndexFeed())
        }
    if kind == "DERIVATIVE":
        return {"BIZNESRADAR": BiznesRadarDerivativeFeed()}
    raise ValueError(f"Unsupported market kind: {kind}")
