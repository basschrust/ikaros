"""Regression tests for safe London price/dividend normalization."""

import pandas as pd
import pytest

from run.backfill_equities import normalize_equity_quote_units


def test_lse_nonzero_dividend_does_not_abort_ohlc(caplog):
    original = pd.DataFrame(
        {
            "open": [3600.0, 3650.0],
            "high": [3700.0, 3750.0],
            "low": [3500.0, 3600.0],
            "close": [3680.0, 3710.0],
            "volume": [100, 200],
            "dividend": [0.0, 25.0],
            "split_ratio": [None, None],
        }
    )
    converted = normalize_equity_quote_units(original, "LSE", "SHEL.L")
    assert converted["close"].tolist() == [36.8, 37.1]
    assert converted["volume"].tolist() == [100, 200]
    assert converted["dividend"].iloc[0] == 0.0
    assert pd.isna(converted["dividend"].iloc[1])
    assert original["close"].iloc[0] == 3680.0  # no mutation
    assert "dividend observation(s) set to NULL" in caplog.text


def test_other_venues_unchanged():
    frame = pd.DataFrame({"close": [123.0], "dividend": [3.0]})
    assert normalize_equity_quote_units(frame, "BSE", "OTP.BD") is frame


def test_unverified_lse_ticker_is_rejected():
    frame = pd.DataFrame({"close": [500.0], "dividend": [0.0]})
    with pytest.raises(ValueError, match="not verified"):
        normalize_equity_quote_units(frame, "LSE", "UNKNOWN.L")
