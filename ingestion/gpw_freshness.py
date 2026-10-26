"""Pure functions for GPW end-of-day data freshness reporting.

A weekday-based approximation until Ikaros' GPW exchange calendar is wired in.
"""

from __future__ import annotations

from datetime import date, timedelta


def expected_last_session(as_of: date, *, include_today: bool = False) -> date:
    """Most recent weekday; defaults to last *completed* weekday before as_of.

    This is deliberately a weekday heuristic, NOT an exchange calendar.
    """
    day = as_of if include_today else as_of - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def weekdays_behind(last_observation: date | None, expected: date) -> int | None:
    """Count missing weekdays after the last available observation through expected."""
    if last_observation is None:
        return None
    if last_observation >= expected:
        return 0
    days = 0
    current = last_observation + timedelta(days=1)
    while current <= expected:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def freshness_status(lag: int | None, max_lag_sessions: int = 0) -> str:
    if max_lag_sessions < 0:
        raise ValueError("max_lag_sessions cannot be negative")
    if lag is None:
        return "NO_DATA"
    return "CURRENT" if lag <= max_lag_sessions else "STALE"
