from utils.dateutils import dates_in_year, working_days_in_year


def test_dates_in_leap_year():
    days = dates_in_year(2024)
    assert len(days) == 366
    assert days[0] == "2024-01-01"
    assert "2024-02-29" in days
    assert days[-1] == "2024-12-31"


def test_working_days_exclude_weekends():
    days = working_days_in_year(2025)
    assert "2025-01-01" in days  # Holidays are handled separately by calendars.
    assert "2025-01-04" not in days
    assert "2025-01-05" not in days
    assert "2025-01-06" in days
    assert len(days) == 261
