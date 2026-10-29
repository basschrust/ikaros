from datetime import date
import pandas as pd
import pytest
from ingestion.index_prices_refresh import aggregate_hourly_index_prices


def test_hourly_grouped_by_warsaw_session_including_utc_near_midnight():
    # 6 consecutive bars per date, one UTC timestamp shifted into next Warsaw day
    t = pd.date_range("2026-10-27 09:00", periods=6, freq="h", tz="Europe/Warsaw")
    t2 = pd.date_range("2026-10-28 09:00", periods=6, freq="h", tz="Europe/Warsaw")
    idx = t.append(t2)
    raw = pd.DataFrame(
        {
            "Open": [100 + i for i in range(12)],
            "High": [101 + i for i in range(12)],
            "Low": [99 + i for i in range(12)],
            "Close": [100.5 + i for i in range(12)],
        },
        index=idx,
    )
    raw.index.name = "Datetime"
    out = aggregate_hourly_index_prices(raw, start=date(2026, 10, 27), end=date(2026, 10, 28))
    assert out["date"].tolist() == [date(2026, 10, 27), date(2026, 10, 28)]
    assert out["open"].tolist() == [100, 106]
    assert out["close"].tolist() == [105.5, 111.5]
    assert out["high"].tolist() == [106, 112]


def test_incomplete_session_excluded():
    idx = pd.date_range("2026-10-27 10:00", periods=3, freq="h", tz="Europe/Warsaw")
    raw = pd.DataFrame(
        {"Open": [100] * 3, "High": [101] * 3, "Low": [99] * 3, "Close": [100] * 3}, index=idx
    )
    raw.index.name = "Datetime"
    assert aggregate_hourly_index_prices(
        raw, start=date(2026, 10, 27), end=date(2026, 10, 27)
    ).empty


def test_naive_hourly_timestamps_rejected():
    idx = pd.date_range("2026-10-27 10:00", periods=6, freq="h")
    raw = pd.DataFrame(
        {"Open": [100] * 6, "High": [101] * 6, "Low": [99] * 6, "Close": [100] * 6}, index=idx
    )
    raw.index.name = "Datetime"
    with pytest.raises(ValueError, match="timezone"):
        aggregate_hourly_index_prices(raw, start=date(2026, 10, 27), end=date(2026, 10, 27))
