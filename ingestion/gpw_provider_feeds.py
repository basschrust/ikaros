"""Independent GPW OHLCV adapters, with explicit upstream diagnostics.

Symbols are resolved by PROVIDER_SYMBOLS, never guessed inside an adapter.
All returned price fields are Decimal and volume is an integer. Providers
must return actual, complete source candles, never synthetic substitute rows.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import pandas as pd
import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class RawCandle:
    day: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None


class GPWPriceFeed(Protocol):
    code: str

    def history(self, symbol: str, start: date, end: date) -> list[RawCandle]: ...


def _number(value: object) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if not text or text.lower() in {"-", "--", "n/a", "none", "nan"}:
        return None
    try:
        result = Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Bad quote {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Nonfinite quote {value!r}")
    return result


def _volume(value: object) -> int | None:
    quantity = _number(value)
    if quantity is None:
        return None
    if quantity != quantity.to_integral_value():
        raise ValueError(f"Fractional volume: {value!r}")
    return int(quantity)


def _one(
    day: object,
    op: object,
    high: object,
    low: object,
    close: object,
    volume: object,
) -> RawCandle:
    if isinstance(day, datetime):
        parsed = day.date()
    elif isinstance(day, date):
        parsed = day
    else:
        value = str(day).strip()
        parsed = (
            date.fromisoformat(value)
            if "-" in value
            else datetime.strptime(value, "%d.%m.%Y").date()
        )
    return RawCandle(
        parsed, _number(op), _number(high), _number(low), _number(close), _volume(volume)
    )


def _dedupe(rows: list[RawCandle]) -> list[RawCandle]:
    seen: set[date] = set()
    for candle in rows:
        if candle.day in seen:
            raise ValueError(f"Duplicate provider session {candle.day}")
        seen.add(candle.day)
    return sorted(rows, key=lambda item: item.day)


@dataclass
class YahooFinanceFeed:
    code: str = "YFINANCE"

    def history(self, symbol: str, start: date, end: date) -> list[RawCandle]:
        from ingestion.prices import fetch_eod_prices

        frame = fetch_eod_prices(
            symbol=symbol,
            start_date=start.isoformat(),
            end_date=(end + timedelta(days=1)).isoformat(),
            provider="yfinance",
        )
        if frame.empty:
            raise ValueError(
                f"Yahoo Finance returned no OHLCV for {symbol} in {start}..{end}; "
                "check OpenBB response, ticker, or upstream availability"
            )
        wanted = {"date", "open", "high", "low", "close", "volume"}
        if not wanted <= set(frame.columns):
            raise ValueError("Yahoo returned incomplete OHLCV columns")
        rows = _dedupe(
            [
                _one(row.date, row.open, row.high, row.low, row.close, row.volume)
                for row in frame.itertuples(index=False)
                if start <= pd.Timestamp(row.date).date() <= end
            ]
        )
        if not rows:
            raise ValueError(
                f"Yahoo Finance returned data but no sessions within {start}..{end} for {symbol}"
            )
        return rows


@dataclass
class StooqFeed:
    code: str = "STOOQ"
    session: requests.Session | None = None
    timeout: float = 20.0
    # Optional key from Stooq itself; never persist or print this secret.
    api_key: str | None = None

    def history(self, symbol: str, start: date, end: date) -> list[RawCandle]:
        client = self.session or requests
        params = {
            "s": symbol.lower(),
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        key = (self.api_key or os.getenv("IKAROS_STOOQ_APIKEY") or "").strip()
        if key:
            params["apikey"] = key
        try:
            response = client.get(
                "https://stooq.com/q/d/l/",
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "Ikaros/0.3 (market data research)"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # Do not leak the API key through exception URLs or debug output.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403}:
                raise ValueError("Stooq denied CSV access (HTTP 401/403); check API key") from exc
            if status == 429:
                raise ValueError("Stooq daily download quota exceeded (HTTP 429)") from exc
            raise ValueError(f"Stooq request failed: {type(exc).__name__}") from exc
        data = response.text.lstrip("\ufeff").strip()
        if not data:
            raise ValueError(f"Stooq returned an empty HTTP response for {symbol}")
        if data.lower().startswith("no data"):
            raise ValueError(f"Stooq returned no data for {symbol}; verify symbol and dates")
        lower = data[:3000].lower()
        if any(
            marker in lower
            for marker in ("exceeded the daily hits limit", "daily hits limit", "too many requests")
        ):
            raise ValueError("Stooq daily download quota exceeded; retry after quota resets")
        if any(
            marker in lower
            for marker in ("apikey", "api key", "access denied", "unauthorized", "forbidden")
        ):
            raise ValueError(
                "Stooq historical CSV requires valid API-key authorization; "
                "set IKAROS_STOOQ_APIKEY (see Stooq's own key instructions)"
            )
        if "<html" in lower or "<!doctype html" in lower or "captcha" in lower:
            raise ValueError(
                "Unexpected Stooq format: HTML/challenge response instead of authorized CSV data"
            )

        # Allow a UTF-8 BOM and either comma or semicolon-delimited daily CSV.
        first_line = data.splitlines()[0]
        delimiter = ";" if ";" in first_line and "," not in first_line else ","
        reader = csv.DictReader(io.StringIO(data), delimiter=delimiter)
        required = {"date", "open", "high", "low", "close", "volume"}
        headers = {name.strip().lower().lstrip("\ufeff") for name in reader.fieldnames or []}
        if not required <= headers:
            raise ValueError(
                f"Unexpected Stooq CSV columns for {symbol}: {sorted(headers)}; "
                "check whether the endpoint returned an API-key/quota message"
            )
        rows: list[RawCandle] = []
        for record in reader:
            values = {
                key.strip().lower().lstrip("\ufeff"): val for key, val in record.items() if key
            }
            day = date.fromisoformat(values["date"].strip())
            if start <= day <= end:
                rows.append(
                    _one(
                        day,
                        values["open"],
                        values["high"],
                        values["low"],
                        values["close"],
                        values["volume"],
                    )
                )
        if not rows:
            raise ValueError(f"Stooq CSV contained no sessions within {start}..{end} for {symbol}")
        return _dedupe(rows)


def _unaccent(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )


def _history_heading(soup: BeautifulSoup, symbol: str) -> bool:
    """Identify the instrument using page headings, not unrelated link text."""
    # Prefer H1 over TITLE; title often has the full company name before (ALE).
    headings = soup.find_all("h1") or soup.find_all("title")
    ticker = re.compile(rf"(?<![a-z0-9]){re.escape(symbol.lower())}(?![a-z0-9])")
    return any(
        "notowania historyczne" in (text := _unaccent(h.get_text(" ", strip=True)))
        and ticker.search(text)
        for h in headings
    )


def _column_indices(table: BeautifulSoup) -> dict[str, int] | None:
    header_row = table.find("thead")
    first = header_row.find("tr") if header_row else table.find("tr")
    if not first:
        return None
    columns = [
        _unaccent(cell.get_text(" ", strip=True)).strip()
        for cell in first.find_all(("td", "th"), recursive=False)
    ]
    aliases = {
        "date": ("data", "date"),
        "open": ("otwarcie", "open"),
        "high": ("max", "maks", "high"),
        "low": ("min", "low"),
        "close": ("zamkniecie", "close"),
        "volume": ("wolumen", "volume"),
    }
    positions: dict[str, int] = {}
    for field, labels in aliases.items():
        matches = [i for i, label in enumerate(columns) if label in labels]
        if len(matches) != 1:
            return None
        positions[field] = matches[0]
    return positions


def parse_biznesradar_html(html: str, symbol: str) -> list[RawCandle]:
    """Parse real GPW historical-table rows, failing closed on errors/challenges."""
    soup = BeautifulSoup(html, "html.parser")
    lower = _unaccent(soup.get_text(" ", strip=True))
    if any(
        marker in lower[:1500]
        for marker in ("captcha", "verify you are human", "access denied", "checking your browser")
    ):
        raise ValueError("BiznesRadar returned an access challenge; no source candles available")
    if not _history_heading(soup, symbol):
        raise ValueError(
            f"BiznesRadar returned another instrument, nonhistorical page, or challenge for {symbol}"
        )
    if "gpw" not in lower or "akcje" not in lower:
        raise ValueError("BiznesRadar response is not identified as GPW equities")
    rows: list[RawCandle] = []
    found_table = False
    for table in soup.find_all("table"):
        indices = _column_indices(table)
        if indices is None:
            continue
        found_table = True
        for row in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all(("td", "th"), recursive=False)
            ]
            if not cells or len(cells) <= max(indices.values()):
                continue
            date_str = cells[indices["date"]]
            if not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", date_str):
                continue
            parsed = datetime.strptime(date_str, "%d.%m.%Y").date()
            rows.append(
                _one(
                    parsed,
                    cells[indices["open"]],
                    cells[indices["high"]],
                    cells[indices["low"]],
                    cells[indices["close"]],
                    cells[indices["volume"]],
                )
            )
    if not found_table:
        raise ValueError(f"No daily OHLCV history table found for BiznesRadar {symbol}")
    if not rows:
        raise ValueError(
            f"BiznesRadar {symbol}: matching history table contains no parseable daily rows"
        )
    return _dedupe(rows)


@dataclass
class BiznesRadarFeed:
    code: str = "BIZNESRADAR"
    session: requests.Session | None = None
    timeout: float = 20.0
    max_pages: int = 30

    def history(self, symbol: str, start: date, end: date) -> list[RawCandle]:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", symbol, re.IGNORECASE):
            raise ValueError("Invalid BiznesRadar symbol")
        client = self.session or requests
        found: dict[date, RawCandle] = {}
        previous_oldest: date | None = None
        for page in range(1, self.max_pages + 1):
            url = f"https://www.biznesradar.pl/notowania-historyczne/{symbol}"
            if page > 1:
                url += f",{page}"
            response = client.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": "Ikaros/0.3 (market data research)", "Accept": "text/html"},
            )
            response.raise_for_status()
            candles = parse_biznesradar_html(response.text, symbol)
            for candle in candles:
                if start <= candle.day <= end:
                    if candle.day in found and found[candle.day] != candle:
                        raise ValueError(f"BiznesRadar conflicting candle for {candle.day}")
                    found[candle.day] = candle
            oldest = min(item.day for item in candles)
            if oldest <= start or (previous_oldest is not None and oldest >= previous_oldest):
                break
            previous_oldest = oldest
        else:
            raise ValueError("BiznesRadar history exceeds max_pages; narrow date range")
        if not found:
            raise ValueError(
                f"BiznesRadar {symbol}: history pages contained no sessions in {start}..{end}"
            )
        return _dedupe(list(found.values()))


def default_feeds() -> dict[str, GPWPriceFeed]:
    return {feed.code: feed for feed in (YahooFinanceFeed(), StooqFeed(), BiznesRadarFeed())}
