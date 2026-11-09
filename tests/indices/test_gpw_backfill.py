from datetime import date

import pandas as pd
import pytest

from ingestion.gpw_backfill import (
    missing_observations,
    normalize_prices,
    normalize_securities,
    year_windows,
)


def test_year_windows():
    assert year_windows(date(2023, 12, 30), date(2025, 1, 2)) == [
        (date(2023, 12, 30), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 1, 2)),
    ]


def test_year_windows_invalid():
    with pytest.raises(ValueError, match="end must not"):
        year_windows(date(2025, 1, 2), date(2025, 1, 1))


def test_normalize_securities():
    actual = normalize_securities(
        pd.DataFrame({"ticker": [" pkn ", "ABC.WA"], "name": ["PKN", "ABC"]})
    )
    assert actual["symbol"].tolist() == ["PKN.WA", "ABC.WA"]


def test_duplicate_securities_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_securities(pd.DataFrame({"ticker": ["PKN", "PKN.WA"], "name": ["one", "two"]}))


def sample_prices():
    return pd.DataFrame(
        [
            dict(
                symbol="ABC.WA",
                date="2024-01-03",
                open=12,
                high=14,
                low=11,
                close=13,
                volume=100,
                dividend=None,
                split_ratio=None,
            ),
            dict(
                symbol="ABC.WA",
                date="2024-01-03",
                open=12,
                high=14,
                low=11,
                close=13.5,
                volume=101,
                dividend=None,
                split_ratio=None,
            ),
            dict(
                symbol="ABC.WA",
                date="2024-01-04",
                open=13,
                high=15,
                low=12,
                close=14,
                volume=102,
                dividend=None,
                split_ratio=None,
            ),
        ]
    )


def test_normalize_prices_deduplicates_and_filters():
    result = normalize_prices(sample_prices(), "ABC.WA", date(2024, 1, 3), date(2024, 1, 3))
    assert len(result) == 1
    assert result.iloc[0]["close"] == 13.5


def test_missing_observations():
    result = normalize_prices(sample_prices(), "ABC.WA", date(2024, 1, 1), date(2024, 12, 31))
    left = missing_observations(result, {date(2024, 1, 3)})
    assert left["date"].tolist() == [date(2024, 1, 4)]


def test_bad_close_rejected():
    frame = sample_prices()
    frame.loc[:, "close"] = -1
    with pytest.raises(ValueError, match="Closing"):
        normalize_prices(frame, "ABC.WA", date(2024, 1, 1), date(2024, 12, 31))
