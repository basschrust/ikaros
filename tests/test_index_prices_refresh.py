from datetime import date
import pandas as pd
import pytest
from ingestion.index_prices_refresh import normalize_yahoo_index_prices


def test_normalizes_flat_columns():
    raw = pd.DataFrame(
        {"Open": [100], "High": [102], "Low": [99], "Close": [101]},
        index=pd.to_datetime(["2026-10-28"]),
    )
    raw.index.name = "Date"
    result = normalize_yahoo_index_prices(raw, start=date(2026, 10, 27), end=date(2026, 10, 28))
    assert result.iloc[0]["close"] == 101


def test_normalizes_yfinance_multiindex():
    raw = pd.DataFrame(
        [[100, 102, 99, 101]],
        index=pd.to_datetime(["2026-10-28"]),
        columns=pd.MultiIndex.from_tuples(
            [("Open", "WIG20.WA"), ("High", "WIG20.WA"), ("Low", "WIG20.WA"), ("Close", "WIG20.WA")]
        ),
    )
    raw.index.name = "Date"
    assert (
        len(normalize_yahoo_index_prices(raw, start=date(2026, 10, 28), end=date(2026, 10, 28)))
        == 1
    )


def test_rejects_bad_ohlc():
    raw = pd.DataFrame(
        {"Open": [100], "High": [101], "Low": [99], "Close": [102]},
        index=pd.to_datetime(["2026-10-28"]),
    )
    raw.index.name = "Date"
    with pytest.raises(ValueError, match="Inconsistent"):
        normalize_yahoo_index_prices(raw, start=date(2026, 10, 28), end=date(2026, 10, 28))


def test_excludes_future_session():
    raw = pd.DataFrame(
        {"Open": [100], "High": [102], "Low": [99], "Close": [101]},
        index=pd.to_datetime(["2026-10-29"]),
    )
    raw.index.name = "Date"
    assert normalize_yahoo_index_prices(raw, start=date(2026, 10, 20), end=date(2026, 10, 28)).empty
