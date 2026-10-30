"""Offline, provider-agnostic tests: no Kafka, OpenBB, or Snowflake needed."""
from datetime import date

import pandas as pd
import pytest

from ikaros_app.collection import DailyCollector, convert_daily_rows
from ikaros_app.providers import ProviderRegistry


class FakeProvider:
    dataset = "GPW"
    name = "fake"

    def history(self, symbol, start, end):
        return pd.DataFrame([
            {"date": date(2026, 10, 28), "open": 99, "high": 101, "low": 98, "close": 100, "volume": 100},
            {"date": date(2026, 10, 29), "open": 100, "high": 101, "low": 99, "close": None, "volume": 0},
        ])


def test_provider_registry():
    registry = ProviderRegistry((FakeProvider(),))
    assert registry.describe() == {"GPW": "fake"}
    assert registry.get("gpw").name == "fake"
    with pytest.raises(ValueError, match="No daily price provider"):
        registry.get("FX")


def test_collector_skips_null_close_and_keeps_valid_day():
    collector = DailyCollector(ProviderRegistry((FakeProvider(),)))
    rows = collector.collect(dataset="GPW", symbol="ANR.WA", days=7, today=date(2026, 10, 29))
    assert len(rows) == 1
    assert rows[0].day == date(2026, 10, 28)
    assert rows[0].close == 100


def test_duplicate_date_is_rejected():
    frame = pd.DataFrame([
        {"date": "2026-10-28", "close": 1.0},
        {"date": "2026-10-28", "close": 2.0},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        convert_daily_rows(frame, dataset="FX", symbol="EURPLN=X", start=date(2026, 10, 27), end=date(2026, 10, 29))


def test_registry_rejects_duplicate_dataset():
    with pytest.raises(ValueError, match="Duplicate provider"):
        ProviderRegistry((FakeProvider(), FakeProvider()))
