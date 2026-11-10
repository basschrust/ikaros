"""Offline validation of LSE/BSE equity extension."""

from datetime import date

import pandas as pd
import pytest

from run.seed_equities import VENUE_CCY, normalize_europe_securities
from run.backfill_equities import (
    VENUES,
    LSE_PENCE_SYMBOLS,
    normalize_equity_quote_units,
)
from ikaros_app.update import Instrument, build_plan
from ingestion.iic import BatchKey


@pytest.mark.parametrize(
    "venue,symbol,currency", [("LSE", "SHEL.L", "GBP"), ("BSE", "OTP.BD", "HUF")]
)
def test_venue_and_symbols(venue, symbol, currency):
    assert venue in VENUES
    assert VENUE_CCY[venue] == currency
    data = pd.DataFrame({"ticker": [symbol.lower()], "name": [" Example "]})
    result = normalize_europe_securities(data, venue)
    assert result.iloc[0].to_dict() == {"symbol": symbol, "name": "Example"}
    assert BatchKey(venue, symbol, "yfinance", date(2026, 11, 1), date(2026, 11, 9))


def test_wrong_suffix_fails():
    with pytest.raises(ValueError, match="suffix"):
        normalize_europe_securities(pd.DataFrame({"ticker": ["SHEL.BD"], "name": ["Shell"]}), "LSE")


def test_lse_price_conversion_and_budapest_unchanged():
    data = pd.DataFrame(
        {
            "open": [3650.0],
            "high": [3700.0],
            "low": [3600.0],
            "close": [3680.0],
            "volume": [12000],
            "dividend": [None],
            "split_ratio": [None],
        }
    )
    assert "SHEL.L" in LSE_PENCE_SYMBOLS
    converted = normalize_equity_quote_units(data, "LSE", "SHEL.L")
    assert converted.loc[0, "close"] == 36.8
    assert converted.loc[0, "volume"] == 12000
    assert data.loc[0, "close"] == 3680.0
    assert normalize_equity_quote_units(data, "BSE", "OTP.BD") is data


def test_unverified_lse_and_ambiguous_dividends_rejected():
    frame = pd.DataFrame(
        {
            "open": [100],
            "high": [100],
            "low": [100],
            "close": [100],
            "dividend": [None],
        }
    )
    with pytest.raises(ValueError, match="not verified"):
        normalize_equity_quote_units(frame, "LSE", "UNKNOWN.L")
    frame.loc[0, "dividend"] = 7.5
    with pytest.raises(ValueError, match="dividends"):
        normalize_equity_quote_units(frame, "LSE", "SHEL.L")


def test_incremental_planner():
    plan = build_plan(
        {
            "LSE": [Instrument("SHEL.L", date(2026, 11, 6))],
            "BSE": [Instrument("OTP.BD", date(2026, 11, 6))],
        },
        target=date(2026, 11, 9),
    )
    assert {j.dataset for j in plan.jobs} == {"LSE", "BSE"}
    assert all("run.backfill_equities" in j.argv for j in plan.jobs)
    assert all("--write" not in j.argv for j in plan.jobs)
