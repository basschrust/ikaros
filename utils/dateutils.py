# helper functions

from datetime import date, timedelta


def dates_in_year(year: int) -> list[str]:
    current = date(year=year, month=1, day=1)
    end = date(year=year + 1, month=1, day=1)

    dates = []
    while current < end:
        dates.append(current.isoformat())
        current += timedelta(days=1)

    return dates


def working_days_in_year(year: int) -> list[str]:
    current = date(year=year, month=1, day=1)
    end = date(year=year + 1, month=1, day=1)

    dates = []
    while current < end:
        if current.weekday() < 5:  # 0 Mon, 6 Sun
            dates.append(current.isoformat())
        current += timedelta(days=1)

    return dates
