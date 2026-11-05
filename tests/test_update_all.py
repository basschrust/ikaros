"""Pure incremental refresh planner tests; no Snowflake or provider calls."""

from datetime import date

import pytest

from ikaros_app.update import Instrument, build_plan, latest_start

TARGET = date(2026, 11, 2)


def test_latest_date_per_symbol_and_source_selection():
    plan = build_plan(
        {
            "GPW": [
                Instrument("PKO.WA", date(2026, 10, 30)),
                Instrument("KGH.WA", date(2026, 10, 29)),
            ],
            "FX": [
                Instrument("EURPLN=X", date(2026, 10, 30)),
                Instrument("HUFPLN=X", date(2026, 10, 30)),
                Instrument("EURCZK=X", date(2026, 10, 30)),
            ],
            "WIG20": [Instrument("WIG20", date(2026, 10, 30))],
            "FW20": [Instrument("FW20Z2620", date(2026, 10, 30), date(2026, 12, 18))],
        },
        target=TARGET,
    )
    assert {job.dataset for job in plan.jobs} == {"GPW", "FX-YAHOO", "FX-NBP", "WIG20", "FW20"}
    assert any("EURCZK=X: no configured direct feed" in msg for msg in plan.skipped)
    assert all("--write" not in job.argv for job in plan.jobs)
    gpw = [job for job in plan.jobs if job.dataset == "GPW"]
    assert len(gpw) == 2  # distinct latest dates must not be collapsed
    assert next(j for j in plan.jobs if j.dataset == "FX-NBP").symbols == ("HUFPLN=X",)


def test_skips_no_history_stale_and_expired():
    plan = build_plan(
        {
            "GPW": [Instrument("NEW.WA", None), Instrument("OLD.WA", date(2020, 1, 1))],
            "FW20": [Instrument("FW20U2620", date(2026, 8, 1), date(2026, 9, 18))],
        },
        target=TARGET,
    )
    assert not plan.jobs
    assert len(plan.skipped) == 3


def test_bootstrap_is_explicit_and_bounded():
    plan = build_plan(
        {"GPW": [Instrument("NEW.WA", None)]}, target=TARGET, bootstrap_start=date(2026, 10, 30)
    )
    assert len(plan.jobs) == 1
    assert plan.jobs[0].start == date(2026, 10, 30)


def test_index_refresh_days():
    plan = build_plan({"WIG20": [Instrument("WIG20", date(2026, 10, 30))]}, target=TARGET)
    assert plan.jobs[0].argv[-2:] == ("--days", "5")


def test_futures_long_gap_needs_full_backfill():
    plan = build_plan(
        {"FW20": [Instrument("FW20Z2620", date(2026, 9, 1), date(2026, 12, 18))]}, target=TARGET
    )
    assert not plan.jobs
    assert "full-page backfill" in plan.skipped[0]


def test_latest_start():
    assert latest_start(date(2026, 10, 30), TARGET, overlap=2, bootstrap=None) == date(2026, 10, 29)
    assert latest_start(None, TARGET, overlap=2, bootstrap=None) is None


def test_invalid_parameters():
    with pytest.raises(ValueError):
        build_plan({}, target=TARGET, overlap_days=-1)
