from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from ingestion.prices import fetch_eod_prices


def test_eod_prices_uses_provider_endpoint(monkeypatch):
    import ingestion.prices as prices

    fake = MagicMock()
    fake.equity.price.historical.return_value.to_dataframe.return_value = pd.DataFrame(
        {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [100]},
        index=pd.Index([pd.Timestamp("2024-01-02")], name="date"),
    )
    monkeypatch.setattr(prices, "obb", fake)
    result = fetch_eod_prices(symbol="06N.WA", start_date="2024-01-01", end_date="2024-01-04")
    fake.equity.price.historical.assert_called_once_with(
        symbol="06N.WA",
        start_date="2024-01-01",
        end_date="2024-01-04",
        interval="1d",
        provider="yfinance",
    )
    assert result.iloc[0]["date"] == date(2024, 1, 2)
    assert result.iloc[0]["close"] == 10.5
