from datetime import date, datetime, timezone

import pandas as pd
import pytest

from run.collect_us_option_chains import normalize_chain

NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)
EXPIRY = date(2026, 11, 20)


def row(**kw):
    return dict(
        contractSymbol="AAPL261120C00200000",
        strike=200.0, bid=3.5, ask=3.6, lastPrice=3.55,
        volume=120, openInterest=840, impliedVolatility=0.24,
        lastTradeDate="2026-11-10T15:30:00Z",
        **kw,
    )


def test_normalizes_valid_chain():
    result = normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([row()]), NOW)
    assert len(result) == 1
    assert result[0]["currency"] == "USD"
    assert result[0]["quote_date"] == NOW.date()
    assert result[0]["strike"] == 200.0
    assert result[0]["last_trade_at"].tzinfo is None


def test_adjusted_root_rejected():
    bad = row()
    bad["contractSymbol"] = "AAPL1261120C00200000"
    with pytest.raises(ValueError, match="Invalid OCC|adjusted"):
        normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([bad]), NOW)


def test_strike_mismatch_rejected():
    bad = row()
    bad["strike"] = 201.0
    with pytest.raises(ValueError, match="Strike mismatch"):
        normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([bad]), NOW)


def test_negative_quote_rejected():
    bad = row()
    bad["bid"] = -1
    with pytest.raises(ValueError, match="Invalid bid"):
        normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([bad]), NOW)


def test_duplicate_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([row(), row()]), NOW)


def test_null_ask_allowed():
    bad = row()
    bad["ask"] = None
    assert normalize_chain("AAPL", EXPIRY, "CALL", pd.DataFrame([bad]), NOW)[0]["ask"] is None
