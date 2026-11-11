"""Offline parsers and metadata checks for additional official interest rates."""

from datetime import date

import pytest

from ikaros_app.rates_international import (
    configured_series,
    parse_boe_csv,
    parse_estr_csv,
    parse_snb_csv,
)
from run.backfill_rates import select_series


def test_catalog_and_selection():
    specs = configured_series()
    assert {s.currency for s in specs.values()} == {"GBP", "EUR", "CHF"}
    all_selected, sources = select_series(None)
    assert "UST_PAR_USD_10Y" in all_selected
    assert {"BOE", "ECB_EST", "SNB"} <= sources
    selected, sources = select_series(["BOE_SONIA_GBP_ON"])
    assert list(selected) == ["BOE_SONIA_GBP_ON"]
    assert sources == {"BOE"}
    with pytest.raises(ValueError, match="Unknown"):
        select_series(["INVALID"])


def test_boe_two_series_and_missing():
    csv = "DATE,IUDBEDR,IUDSOIA\n03 Nov 2026,4.00,3.90\n04 Nov 2026,4.00,n/a\n"
    result = parse_boe_csv(csv, date(2026, 11, 3), date(2026, 11, 4))
    assert len(result) == 3
    assert next(r.rate for r in result if r.series_id == "BOE_SONIA_GBP_ON") == 0.039
    with pytest.raises(ValueError, match="missing expected"):
        parse_boe_csv("bad,data\na,b", date(2026, 1, 1), date(2026, 12, 31))


def test_estr_negative_rate_and_key_verification():
    csv = "TIME_PERIOD,OBS_VALUE,KEY\n2026-11-03,-0.55,EST.B.EU000A2X2A25.WT\n"
    result = parse_estr_csv(csv, date(2026, 11, 1), date(2026, 11, 10))
    assert len(result) == 1 and result[0].rate == pytest.approx(-0.0055)
    with pytest.raises(ValueError, match="Unexpected"):
        parse_estr_csv(csv.replace("EU000A2X2A25", "WRONG"), date(2026, 11, 1), date(2026, 11, 10))


def test_snb_month_end_and_dimension_filter():
    csv = "CubeId;snboffzisa\nPublishingDate;2026-10-20\nDate;Dimension;Value\n2026-08;LZ;0.00\n2026-09;LZ;0.25\n2026-09;EF;5.00\n"
    records = parse_snb_csv(csv, date(2026, 8, 1), date(2026, 9, 30))
    assert [(r.day.isoformat(), r.rate) for r in records] == [
        ("2026-08-31", 0.0),
        ("2026-09-30", 0.0025),
    ]
    with pytest.raises(ValueError, match="header"):
        parse_snb_csv("bad", date(2026, 1, 1), date(2026, 12, 31))
