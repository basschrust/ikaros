"""Offline tests for rate curve plotting selection; no Snowflake required."""

from datetime import date

import pytest

from ikaros_app.yield_curve_plot import YieldPoint, grouped_points, years_from_tenor, fetch_points


def make_point(series_id, source, kind, tenor, day, rate=0.04):
    return YieldPoint(
        series_id, "GBP", source, kind, tenor, years_from_tenor(tenor), "PUBLISHED", day, rate
    )


def test_tenors():
    assert years_from_tenor("6M") == 0.5
    assert years_from_tenor("10Y") == 10
    assert years_from_tenor("ON") is None
    assert years_from_tenor("0Y") is None


def test_separates_spot_and_par():
    today = date(2026, 11, 10)
    points = [
        make_point("A", "BOE", "GOV_PAR", "5Y", today),
        make_point("B", "BOE", "GOV_SPOT_FITTED", "5Y", today),
    ]
    groups, _ = grouped_points(points, today)
    assert len(groups) == 2


def test_monthly_staleness_separately():
    today = date(2026, 11, 10)
    points = [
        make_point("A_MONTHLY", "X", "GOV_ZERO_SPOT", "5Y", date(2026, 9, 30)),
        make_point("B", "X", "GOV_ZERO_SPOT", "10Y", date(2026, 9, 30)),
    ]
    groups, warnings = grouped_points(points, today)
    assert sum(map(len, groups.values())) == 1
    assert len(warnings) == 1


def test_duplicate_maturity_rejected():
    day = date(2026, 11, 10)
    pts = [make_point("A", "X", "GOV_PAR", "5Y", day), make_point("B", "X", "GOV_PAR", "5Y", day)]
    with pytest.raises(ValueError, match="Duplicate tenors"):
        grouped_points(pts, day)


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return [
            (
                "BOE_GILT_SPOT_GBP_5Y",
                "GBP",
                "BOE_GILT",
                "GOV_SPOT_FITTED",
                "5Y",
                "PUBLISHED_ZERO_COUPON",
                date(2026, 11, 10),
                0.041,
            )
        ]


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()

    def cursor(self):
        return self.cur


def test_query_is_as_of_and_filter_bound():
    con = FakeConnection()
    points = fetch_points(con, date(2026, 11, 10), ["GBP"])
    assert "QUALIFY ROW_NUMBER()" in con.cur.sql
    assert con.cur.params == (date(2026, 11, 10), "GBP")
    assert points[0].rate == 0.041
