"""Fixture-backed provider adapters; no live network dependency."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
import sys
from types import ModuleType

import pandas as pd
import pytest

from ingestion.gpw_provider_feeds import (
    BiznesRadarFeed,
    StooqFeed,
    YahooFinanceFeed,
    parse_biznesradar_html,
)


HTML = """<html><h1>Notowania historyczne PZU</h1><p>GPW - Akcje</p>
<table><thead><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th><th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr></thead>
<tbody><tr><td>17.11.2026</td><td>75,96</td><td>75,96</td><td>74,78</td><td>75,00</td><td>1 247 844</td><td>9 000 000</td></tr>
<tr><td>16.11.2026</td><td>75.00</td><td>76.00</td><td>74.50</td><td>75.25</td><td>1 123 456</td><td>1 000 000</td></tr>
</tbody></table></html>"""


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class HTTP:
    def __init__(self, text):
        self.text = text
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return Response(self.text)


def test_biznesradar_parse_polish_decimal_and_grouped_volume():
    candles = parse_biznesradar_html(HTML, "PZU")
    assert len(candles) == 2
    assert candles[1].day == date(2026, 11, 17)
    assert candles[1].open == Decimal("75.96")
    assert candles[1].volume == 1247844


def test_biznesradar_rejects_wrong_ticker():
    with pytest.raises(ValueError, match="another instrument"):
        parse_biznesradar_html(HTML, "CDR")


def test_biznesradar_fetch_paginates_and_keeps_date_window():
    http = HTTP(HTML)
    feed = BiznesRadarFeed(session=http, max_pages=3)
    bars = feed.history("PZU", date(2026, 11, 16), date(2026, 11, 17))
    assert len(bars) == 2
    assert len(http.urls) == 1  # History already covers the requested start.
    assert http.urls[0][0].endswith("/PZU")


def test_stooq_csv_format_and_decimal():
    http = HTTP("Date,Open,High,Low,Close,Volume\n2026-11-17,75.96,75.96,74.78,75.00,1247844\n")
    bars = StooqFeed(session=http).history("pzu", date(2026, 11, 17), date(2026, 11, 17))
    assert bars[0].close == Decimal("75.00")
    assert bars[0].volume == 1247844
    assert http.urls[0][1]["params"]["s"] == "pzu"
    assert http.urls[0][1]["params"]["d1"] == "20261117"


def test_stooq_bad_html_is_not_treated_as_prices():
    with pytest.raises(ValueError, match="Unexpected Stooq"):
        StooqFeed(session=HTTP("<html>captcha</html>")).history(
            "pzu", date(2026, 11, 17), date(2026, 11, 17)
        )


def test_yahoo_uses_existing_importer_and_exclusive_end():
    frame = pd.DataFrame(
        [
            {
                "date": date(2026, 11, 17),
                "open": 75.96,
                "high": 75.96,
                "low": 74.78,
                "close": 75.0,
                "volume": 0,
            }
        ]
    )
    fake = ModuleType("ingestion.prices")
    fake.fetch_eod_prices = lambda **kwargs: frame
    with patch.dict(sys.modules, {"ingestion.prices": fake}):
        bars = YahooFinanceFeed().history("PZU.WA", date(2026, 11, 17), date(2026, 11, 17))
    assert bars[0].volume == 0
    assert bars[0].high == Decimal("75.96")
