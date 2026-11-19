"""Realistic provider-response regression tests. No network or Snowflake needed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import ModuleType
from unittest.mock import patch
import sys

import pandas as pd
import pytest

from ingestion.gpw_provider_feeds import (
    BiznesRadarFeed,
    StooqFeed,
    YahooFinanceFeed,
    parse_biznesradar_html,
)

START = date(2026, 11, 16)
END = date(2026, 11, 17)


class Response:
    def __init__(self, body: str) -> None:
        self.text = body

    def raise_for_status(self) -> None:
        return None


class HTTP:
    def __init__(self, *pages: str) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> Response:
        self.calls.append((url, kwargs))
        return Response(self.pages[min(len(self.calls) - 1, len(self.pages) - 1)])


GOOD_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-11-16,74.00,76.00,73.00,75.00,1247844\n"
    "2026-11-17,75.00,76.00,74.50,75.50,1250000\n"
)


def _biznesradar_page(symbol: str = "ALE") -> str:
    return f"""<html><head><title>Notowania historyczne ALLEGRO.EU ({symbol}) - BiznesRadar</title></head>
    <body><h1>Notowania historyczne ALLEGRO.EU ({symbol})</h1>
    <div>GPW - Akcje - Notowania ciągłe</div>
    <table><thead><tr><th>Data</th><th>Otwarcie</th><th>Max</th>
    <th>Min</th><th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr></thead>
    <tbody><tr><th>17.11.2026</th><td>75,00</td><td>76,00</td>
    <td>74,50</td><td>75,50</td><td>1 250 000</td><td>95000000</td></tr>
    <tr><td>16.11.2026</td><td>74,00</td><td>76,00</td>
    <td>73,00</td><td>75,00</td><td>1 247 844</td><td>93000000</td></tr>
    </tbody></table></body></html>"""


def test_stooq_supplies_api_key_without_logging_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IKAROS_STOOQ_APIKEY", "my-private-key")
    http = HTTP(GOOD_CSV)
    rows = StooqFeed(session=http).history("ale", START, END)
    assert len(rows) == 2
    assert rows[0].volume == 1247844
    assert http.calls[0][1]["params"]["apikey"] == "my-private-key"
    assert http.calls[0][1]["params"]["s"] == "ale"


def test_stooq_requires_key_with_useful_error_and_never_echoes_secret() -> None:
    http = HTTP("Download CSV needs API key. Get APIKEY to continue.")
    with pytest.raises(ValueError, match="IKAROS_STOOQ_APIKEY") as error:
        StooqFeed(session=http, api_key="top-secret").history("pzu", START, END)
    assert "top-secret" not in str(error.value)


def test_stooq_detects_daily_limit() -> None:
    with pytest.raises(ValueError, match="quota exceeded"):
        StooqFeed(session=HTTP("Exceeded the daily hits limit")).history("pzu", START, END)


def test_stooq_bom_and_semicolon_csv() -> None:
    http = HTTP(
        "\ufeffDate;Open;High;Low;Close;Volume\n2026-11-17;75.00;76.00;74.50;75.50;1250000\n"
    )
    rows = StooqFeed(session=http).history("pzu", END, END)
    assert rows[0].close == Decimal("75.50")


def test_stooq_rejects_html_challenge() -> None:
    with pytest.raises(ValueError, match="challenge"):
        StooqFeed(session=HTTP("<html>challenge</html>")).history("pzu", START, END)


def test_biznesradar_realistic_company_title_and_th_date_cell() -> None:
    rows = parse_biznesradar_html(_biznesradar_page(), "ALE")
    assert len(rows) == 2
    assert rows[-1].close == Decimal("75.50")
    assert rows[-1].volume == 1250000


def test_biznesradar_skips_empty_history_table_and_finds_next() -> None:
    page = _biznesradar_page()
    fake_header = (
        "<table><thead><tr><th>Data</th><th>Otwarcie</th><th>Max</th>"
        "<th>Min</th><th>Zamknięcie</th><th>Wolumen</th></tr></thead></table>"
    )
    page = page.replace("<table>", fake_header + "<table>", 1)
    assert len(parse_biznesradar_html(page, "ALE")) == 2


def test_biznesradar_challenge_is_error_not_empty_history() -> None:
    page = "<html><h1>Verify you are human</h1><div>captcha</div></html>"
    with pytest.raises(ValueError, match="challenge"):
        parse_biznesradar_html(page, "ALE")


def test_biznesradar_wrong_symbol_not_accepted() -> None:
    with pytest.raises(ValueError, match="another instrument"):
        parse_biznesradar_html(_biznesradar_page("PZU"), "ALE")


def test_biznesradar_empty_table_cannot_silently_return_success() -> None:
    html = """<html><h1>Notowania historyczne ALE</h1> GPW - Akcje
        <table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th>
        <th>Zamknięcie</th><th>Wolumen</th></tr></table></html>"""
    with pytest.raises(ValueError, match="no parseable"):
        parse_biznesradar_html(html, "ALE")


def test_biznesradar_paginates_exact_requested_range() -> None:
    http = HTTP(_biznesradar_page("ALE"))
    rows = BiznesRadarFeed(session=http).history("ALE", START, END)
    assert len(rows) == 2
    assert len(http.calls) == 1
    assert http.calls[0][0].endswith("/ALE")


def test_biznesradar_insufficient_pagination_is_explicit_error() -> None:
    http = HTTP(_biznesradar_page("ALE"))
    with pytest.raises(ValueError, match="exceeds max_pages"):
        BiznesRadarFeed(session=http, max_pages=1).history(
            "ALE", date(2020, 1, 1), date(2020, 1, 2)
        )


def test_yahoo_empty_frame_is_not_silent() -> None:
    fake = ModuleType("ingestion.prices")
    fake.fetch_eod_prices = lambda **kwargs: pd.DataFrame()
    with patch.dict(sys.modules, {"ingestion.prices": fake}):
        with pytest.raises(ValueError, match="returned no OHLCV"):
            YahooFinanceFeed().history("PZU.WA", START, END)


def test_yahoo_out_of_window_is_not_silent() -> None:
    fake = ModuleType("ingestion.prices")
    fake.fetch_eod_prices = lambda **kwargs: pd.DataFrame(
        [{"date": date(2025, 1, 1), "open": 50, "high": 52, "low": 49, "close": 51, "volume": 5000}]
    )
    with patch.dict(sys.modules, {"ingestion.prices": fake}):
        with pytest.raises(ValueError, match="no sessions within"):
            YahooFinanceFeed().history("ALE.WA", START, END)
