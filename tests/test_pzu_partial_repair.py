from datetime import date

import pandas as pd
import pytest

from run.repair_equity_prices import plan_repair_partial


def frame():
    return pd.DataFrame(
        {
            "symbol": ["PZU.WA"] * 4,
            "date": [date(2026, 11, day) for day in (2, 3, 4, 5)],
            "open": [70, 70, 70, 70],
            "high": [70, 70, 70, 70],
            "low": [70, 70, 70, 70],
            "close": [70, 70, 70, 70],
            "volume": [0, 0, 0, 500],
        }
    )


def test_only_valid_suspect_candles_planned():
    stored = frame().drop(columns=["symbol"])
    fresh = frame()
    fresh.loc[0, ["open", "high", "low", "close", "volume"]] = [70, 72, 69, 71, 5000]
    fresh.loc[1, "volume"] = 0  # bad source
    fresh = fresh.loc[[0, 1, 3]]  # missing Nov 4, Nov 5 normal stored day
    planned, issues = plan_repair_partial(stored, fresh, "PZU.WA")
    assert planned["date"].tolist() == [date(2026, 11, 2)]
    assert len(issues) == 2
    assert issues[0][0] == date(2026, 11, 3)
    assert issues[0][1].startswith("INVALID_SOURCE")
    assert issues[1] == (date(2026, 11, 4), "MISSING_SOURCE")


def test_reject_duplicate_source_dates():
    stored = frame().drop(columns=["symbol"])
    bad = pd.concat([frame(), frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate replacement dates"):
        plan_repair_partial(stored, bad, "PZU.WA")


def test_reject_wrong_symbol():
    stored = frame().drop(columns=["symbol"])
    bad = frame()
    bad.loc[0, "symbol"] = "OTHER.WA"
    with pytest.raises(ValueError, match="different symbol"):
        plan_repair_partial(stored, bad, "PZU.WA")


def test_all_bad_returns_no_valid_replacement():
    stored = frame().drop(columns=["symbol"])
    planned, issues = plan_repair_partial(stored, frame(), "PZU.WA")
    assert planned.empty
    assert len(issues) == 3
