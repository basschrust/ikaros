from datetime import date
import pandas as pd
import pytest

from run.seed_equities import normalize_europe_securities, VENUE_CCY
from run.backfill_equities import VENUES
from ikaros_app.update import Instrument, build_plan
from ingestion.iic import BatchKey


@pytest.mark.parametrize(
    "venue,symbol,ccy",
    [
        ("PSE", "CEZ.PR", "CZK"),
        ("XETRA", "SAP.DE", "EUR"),
        ("PARIS", "MC.PA", "EUR"),
    ],
)
def test_europe_registry_and_normalization(venue, symbol, ccy):
    assert venue in VENUES
    assert VENUE_CCY[venue] == ccy
    actual = normalize_europe_securities(
        pd.DataFrame({"ticker": [symbol.lower()], "name": [" Example "]}), venue
    )
    assert actual.iloc[0].to_dict() == {"symbol": symbol, "name": "Example"}
    assert BatchKey(venue, symbol, "yfinance", date(2026, 11, 1), date(2026, 11, 9))


def test_reject_mismatched_suffix():
    with pytest.raises(ValueError, match="suffix"):
        normalize_europe_securities(pd.DataFrame({"ticker": ["CEZ.DE"], "name": ["CEZ"]}), "PSE")


def test_europe_planner_uses_shared_worker():
    plan = build_plan(
        {
            "PSE": [Instrument("CEZ.PR", date(2026, 11, 6))],
            "XETRA": [Instrument("SAP.DE", date(2026, 11, 6))],
            "PARIS": [Instrument("MC.PA", date(2026, 11, 6))],
        },
        target=date(2026, 11, 9),
    )
    assert {j.dataset for j in plan.jobs} == {"PSE", "XETRA", "PARIS"}
    assert all("run.backfill_equities" in j.argv for j in plan.jobs)
    assert all("--write" not in j.argv for j in plan.jobs)


def test_no_history_requires_explicit_bootstrap():
    plan = build_plan({"PSE": [Instrument("CEZ.PR", None)]}, target=date(2026, 11, 9))
    assert not plan.jobs
    assert any("bootstrap-start" in s for s in plan.skipped)
