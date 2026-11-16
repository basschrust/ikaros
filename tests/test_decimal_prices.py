from decimal import Decimal
import pytest

from ingestion.decimal_prices import decimal_text, integer_text


def test_quoted_prices_are_fixed_point_strings():
    assert decimal_text(Decimal("73.90")) == "73.90000000"
    assert decimal_text(0.1 + 0.2) == "0.30000000"
    assert decimal_text("73.900000005") == "73.90000001"


def test_null_and_nonfinite():
    assert decimal_text(None) is None
    with pytest.raises(ValueError, match="Nonfinite"):
        decimal_text(float("inf"))


def test_volume_integral():
    assert integer_text(1768077) == "1768077"
    with pytest.raises(ValueError, match="Nonintegral"):
        integer_text("2.5")


def test_overflow_rejected():
    with pytest.raises(ValueError, match="precision"):
        decimal_text("10000000000000000")


def test_repair_validation_keeps_csv_precision():
    from datetime import date
    import pandas as pd
    from ingestion.equity_quality import validate_repair_candles

    row = pd.DataFrame(
        [
            {
                "symbol": "PZU.WA",
                "date": "2026-11-10",
                "open": "73.90",
                "high": "74.60",
                "low": "73.36",
                "close": "74.60",
                "volume": "1768077",
            }
        ]
    )
    result = validate_repair_candles(row, "PZU.WA")
    assert result.loc[0, "open"] == Decimal("73.90")
    assert result.loc[0, "date"] == date(2026, 11, 10)
