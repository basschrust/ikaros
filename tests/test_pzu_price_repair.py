from datetime import date

import pandas as pd
import pytest

from ingestion.equity_quality import suspect_zero_volume, validate_repair_candles
from ingestion.gpw_backfill import normalize_prices
from run.repair_equity_prices import plan_repair


def rows(symbol="PZU.WA"):
    return pd.DataFrame(
        {
            "symbol": [symbol, symbol],
            "date": [date(2026, 11, 9), date(2026, 11, 10)],
            "open": [70.0, 71.0],
            "high": [70.0, 74.0],
            "low": [70.0, 70.5],
            "close": [70.0, 73.0],
            "volume": [0, 1000],
            "dividend": [None, None],
            "split_ratio": [None, None],
        }
    )


def test_incoming_suspect_blocked_but_valid_retained():
    frame = rows()
    clean = normalize_prices(
        frame, "PZU.WA", date(2026, 11, 9), date(2026, 11, 10), skip_invalid=True
    )
    assert clean["date"].tolist() == [date(2026, 11, 10)]
    with pytest.raises(ValueError, match="Suspicious zero-volume"):
        normalize_prices(frame, "PZU.WA", date(2026, 11, 9), date(2026, 11, 10))


def test_real_zero_volume_illiquid_nonflat_not_auto_rejected():
    frame = rows("XYZ.WA")
    frame.loc[0, "high"] = 71.0
    assert not suspect_zero_volume(frame, "XYZ.WA").any()
    assert suspect_zero_volume(rows("XYZ.WA"), "XYZ.WA").tolist() == [True, False]


def test_plan_only_bad_date_repaired():
    existing = rows().drop(columns=["symbol", "dividend", "split_ratio"])
    corrected = rows()
    corrected.loc[0, ["open", "high", "low", "close", "volume"]] = [72, 75, 71, 74, 123456]
    plan = plan_repair(existing, corrected, "PZU.WA")
    assert plan["date"].tolist() == [date(2026, 11, 9)]
    assert plan.iloc[0]["volume"] == 123456


def test_replacement_zero_volume_fails_closed():
    existing = rows().drop(columns=["symbol", "dividend", "split_ratio"])
    with pytest.raises(ValueError, match="positive"):
        plan_repair(existing, rows(), "PZU.WA")


def test_missing_replacement_fails_closed():
    existing = rows().drop(columns=["symbol", "dividend", "split_ratio"])
    with pytest.raises(ValueError, match="no usable replacement"):
        plan_repair(existing, rows().iloc[[1]], "PZU.WA")


def test_duplicate_repair_dates_fail():
    frame = pd.concat([rows().iloc[[1]], rows().iloc[[1]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_repair_candles(frame, "PZU.WA")
