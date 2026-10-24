from datetime import date

import pandas as pd
import pytest

from run.backfill_fx_snowflake import normalize_batch, year_windows


def test_year_windows_cover_inclusive_range():
    assert list(year_windows(date(2023, 10, 1), date(2025, 2, 3))) == [
        (date(2023, 10, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 2, 3)),
    ]


def test_invalid_window():
    with pytest.raises(ValueError):
        list(year_windows(date(2025, 2, 1), date(2024, 1, 1)))


def test_normalize_deduplicates_and_serializes_dates():
    frame = pd.DataFrame([
        {"date": "2024-01-02", "open": 4.1, "high": 4.3, "low": 4.0,
         "close": 4.2, "volume": None},
        {"date": "2024-01-02", "open": 4.1, "high": 4.3, "low": 4.0,
         "close": 4.25, "volume": None},
        {"date": "2024-01-03", "open": 4.2, "high": 4.4, "low": 4.1,
         "close": 4.3, "volume": 100},
    ])
    rows = normalize_batch(frame, 1, "EURPLN=X", date(2024, 1, 1), date(2024, 12, 31))
    assert len(rows) == 2
    assert rows[0] == (1, "EURPLN=X", "2024-01-02", 4.1, 4.3, 4.0, 4.25, None)
    assert rows[1][2] == "2024-01-03"


def test_empty_batch():
    assert normalize_batch(pd.DataFrame(), 1, "EURPLN=X",
                           date(2024, 1, 1), date(2024, 12, 31)) == []
