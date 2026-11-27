from datetime import date

import pytest

from database.wig20_history import sessions


def test_session_dates_exclude_weekends_and_closures():
    assert sessions(
        start=date(2025, 1, 1),
        end=date(2025, 1, 7),
        closures={date(2025, 1, 1), date(2025, 1, 6)},
        years={2025},
    ) == {date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 7)}


def test_unknown_year_not_treated_as_open():
    with pytest.raises(ValueError, match="not maintained"):
        sessions(start=date(2024, 12, 31), end=date(2025, 1, 2), closures=set(), years={2025})
