from datetime import date

import pandas as pd
import pytest

from ingestion.gpw_backfill import normalize_prices


def observations(closes):
    return pd.DataFrame(
        [
            dict(
                symbol="BLO.WA",
                date=f"2026-11-0{i + 2}",
                open=10,
                high=12,
                low=9,
                close=close,
                volume=100,
                dividend=None,
                split_ratio=None,
            )
            for i, close in enumerate(closes)
        ]
    )


def test_tolerant_mode_preserves_valid_day(caplog):
    result = normalize_prices(
        observations([None, 11.5]),
        "BLO.WA",
        date(2026, 11, 2),
        date(2026, 11, 3),
        skip_invalid=True,
    )
    assert result["date"].tolist() == [date(2026, 11, 3)]
    assert result["close"].tolist() == [11.5]
    assert "SKIP INVALID GPW CANDLE BLO.WA 2026-11-02" in caplog.text


def test_tolerant_mode_all_invalid_returns_empty():
    result = normalize_prices(
        observations([None, -1]),
        "BLO.WA",
        date(2026, 11, 2),
        date(2026, 11, 3),
        skip_invalid=True,
    )
    assert result.empty


def test_strict_mode_still_rejects_bad_close():
    with pytest.raises(ValueError, match="Closing prices"):
        normalize_prices(observations([None]), "BLO.WA", date(2026, 11, 2), date(2026, 11, 3))
