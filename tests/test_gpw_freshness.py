from datetime import date

import pytest

from ingestion.gpw_freshness import expected_last_session, freshness_status, weekdays_behind
from run.gpw_freshness import build_report


def test_monday_previous_friday():
    assert expected_last_session(date(2026, 10, 26)) == date(2026, 10, 23)


def test_saturday_previous_friday():
    assert expected_last_session(date(2026, 10, 24)) == date(2026, 10, 23)


def test_include_monday_today():
    assert expected_last_session(date(2026, 10, 26), include_today=True) == date(2026, 10, 26)


def test_one_missing_weekday_over_weekend():
    assert weekdays_behind(date(2026, 10, 22), date(2026, 10, 26)) == 2


def test_up_to_date():
    assert weekdays_behind(date(2026, 10, 23), date(2026, 10, 23)) == 0


def test_no_data():
    assert weekdays_behind(None, date(2026, 10, 23)) is None
    assert freshness_status(None) == "NO_DATA"


def test_threshold():
    assert freshness_status(1, 1) == "CURRENT"
    assert freshness_status(2, 1) == "STALE"
    with pytest.raises(ValueError):
        freshness_status(0, -1)


def test_report():
    rows = [
        ("06N.WA", "Example", 20, date(2026, 10, 1), date(2026, 10, 23), 17),
        ("ZZZ.WA", "Other", 0, None, None, 0),
    ]
    report = build_report(rows, expected=date(2026, 10, 23), allowed_lag=0)
    assert list(report["status"]) == ["CURRENT", "NO_DATA"]
    assert report.loc[0, "rows_in_period"] == 17
