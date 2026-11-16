"""US stock integration: pure validation/planning tests, no network or Snowflake."""

from datetime import date

import pandas as pd
import pytest

from ikaros_app.update import DATASETS, Instrument, build_plan
from run.seed_equities import normalize_us_securities
from run.discover_us_sp500 import parse_constituents


def test_us_symbol_translation_and_collision():
    rows = normalize_us_securities(
        pd.DataFrame(
            {
                "ticker": ["AAPL", "BRK.B", "BF.B"],
                "name": ["Apple", "Berkshire Hathaway B", "Brown-Forman B"],
            }
        )
    )
    assert rows.symbol.tolist() == ["AAPL", "BRK-B", "BF-B"]
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_us_securities(pd.DataFrame({"ticker": ["BRK.B", "BRK-B"], "name": ["A", "B"]}))


def test_us_incremental_worker_and_no_gpwmix():
    target = date(2026, 11, 9)
    p = build_plan(
        {
            "US": [Instrument("AAPL", date(2026, 11, 6))],
            "GPW": [Instrument("PKO.WA", date(2026, 11, 6))],
        },
        target=target,
        datasets=("GPW", "US"),
    )
    assert {j.dataset for j in p.jobs} == {"GPW", "US"}
    assert next(j for j in p.jobs if j.dataset == "US").argv[2:5] == (
        "run.backfill_equities",
        "--venue",
        "US",
    )
    assert "US" in DATASETS


def test_new_us_requires_explicit_initial_backfill():
    p = build_plan({"US": [Instrument("NVDA", None)]}, target=date(2026, 11, 9), datasets=("US",))
    assert not p.jobs
    assert "--bootstrap-start" in p.skipped[0]


def test_wikipedia_layout_fail_closed():
    with pytest.raises(ValueError, match="constituents table"):
        parse_constituents("<html><body>Unavailable</body></html>")


def test_us_batch_key_is_distinct_from_gpw():
    from ingestion.iic import BatchKey

    us = BatchKey("US", "AAPL", "yfinance", date(2026, 11, 2), date(2026, 11, 9))
    gpw = BatchKey("GPW", "AAPL", "yfinance", date(2026, 11, 2), date(2026, 11, 9))
    assert us != gpw
