from datetime import date

import pytest

from ikaros_app.rates_government import (
    BOE_CODES,
    configured_series,
    parse_boe_gilt_csv,
    parse_eurostat_json,
)


def test_catalog_distinguishes_yield_types():
    catalog = configured_series()
    assert len(catalog) == 9
    assert catalog["BOE_GILT_SPOT_GBP_10Y"].curve_type == "GOV_SPOT_FITTED"
    assert catalog["BOE_GILT_PAR_GBP_10Y"].curve_type == "GOV_PAR"
    assert catalog["EUROSTAT_GOV10Y_PLN_MONTHLY"].curve_type == "GOV_BENCHMARK_10Y_MONTHLY"


def test_boe_gilt_csv_and_missing_yields():
    header = "DATE," + ",".join(BOE_CODES)
    row = "02 Jan 2025," + ",".join(["4.5", "5.0", "5.5", "4.4", "N/A", "5.4"])
    values = parse_boe_gilt_csv(header + "\n" + row, date(2025, 1, 1), date(2025, 1, 3))
    assert len(values) == 5
    assert next(v.rate for v in values if v.series_id == "BOE_GILT_PAR_GBP_10Y") == 0.05
    with pytest.raises(ValueError, match="missing columns"):
        parse_boe_gilt_csv("DATE,IUDMNPY\n02 Jan 2025,5.0", date(2025, 1, 1), date(2025, 1, 3))


def test_eurostat_month_end_and_flat_indices():
    payload = {
        "id": ["freq", "geo", "time"],
        "size": [1, 1, 2],
        "dimension": {
            "freq": {"category": {"index": {"M": 0}}},
            "geo": {"category": {"index": {"PL": 0}}},
            "time": {"category": {"index": {"2025-01": 0, "2025-02": 1}}},
        },
        "value": {"0": 5.2, "1": 5.4},
    }
    result = parse_eurostat_json(payload, "PL", date(2025, 1, 1), date(2025, 2, 28))
    assert [r.day for r in result] == [date(2025, 1, 31), date(2025, 2, 28)]
    assert [r.rate for r in result] == pytest.approx([0.052, 0.054])
    with pytest.raises(ValueError, match="missing CZ"):
        parse_eurostat_json(payload, "CZ", date(2025, 1, 1), date(2025, 2, 28))
