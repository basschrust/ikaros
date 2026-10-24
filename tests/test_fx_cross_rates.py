from datetime import date

import pandas as pd
import pytest

from ingestion.fx_cross_rates import CROSSES, CrossSpec, derive_closes


START = date(2024, 1, 1)
END = date(2024, 1, 4)
SPEC = CROSSES["CADPLN=X"]


def frame(dates, closes):
    return pd.DataFrame({"date": dates, "close": closes})


def test_cross_uses_only_matching_dates():
    usdpln = frame(["2024-01-02", "2024-01-03"], [4.0, 4.2])
    usdcad = frame(["2024-01-02", "2024-01-04"], [1.25, 1.3])
    result = derive_closes(usdpln, usdcad, SPEC, start=START, end=END)
    assert len(result) == 1
    assert result.iloc[0]["date"] == date(2024, 1, 2)
    assert result.iloc[0]["close"] == pytest.approx(3.2)
    assert result.iloc[0]["symbol"] == "CADPLN=X"
    assert "USDPLN=X / USDCAD=X" in result.iloc[0]["comment"]


def test_rejects_zero_denominator():
    with pytest.raises(ValueError, match="nonpositive"):
        derive_closes(frame(["2024-01-02"], [4.0]),
                      frame(["2024-01-02"], [0.0]), SPEC, start=START, end=END)


def test_rejects_duplicate_dates():
    with pytest.raises(ValueError, match="duplicate source dates"):
        derive_closes(frame(["2024-01-02", "2024-01-02"], [4, 5]),
                      frame(["2024-01-02"], [1.25]), SPEC, start=START, end=END)


def test_rejects_bad_prices():
    with pytest.raises(ValueError, match="missing or nonpositive"):
        derive_closes(frame(["2024-01-02"], [None]),
                      frame(["2024-01-02"], [1.25]), SPEC, start=START, end=END)


def test_rejects_reversed_range():
    with pytest.raises(ValueError, match="start must"):
        derive_closes(frame([], []), frame([], []), SPEC, start=END, end=START)


def test_specs_have_expected_direction():
    assert CROSSES["CNYPLN=X"] == CrossSpec("CNYPLN=X", "USDPLN=X", "USDCNY=X")
    assert CROSSES["CZKPLN=X"].denominator == "EURCZK=X"
