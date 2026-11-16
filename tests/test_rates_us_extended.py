"""Offline regression checks for new Treasury sources and nullable bid/ask storage."""

from datetime import date

import pytest

from ikaros_app.rates_us_extended import (
    QuoteableRateObservation,
    configured_series,
    parse_real_csv,
    parse_bill_csv,
)
from run.backfill_rates import select_series, SCHEMA_DDL


def test_catalog_19_new_series_and_distinct_conventions():
    series = configured_series()
    assert len(series) == 19
    assert series["UST_REAL_PAR_USD_10Y"].curve_type == "GOV_REAL_PAR"
    assert series["UST_BILL_DISCOUNT_USD_13W"].curve_type == "GOV_BILL_DISCOUNT"
    assert series["UST_BILL_COUPON_USD_13W"].curve_type == "GOV_BILL_COUPON_EQUIV"
    selected, sources = select_series(["UST_REAL_PAR_USD_5Y", "UST_BILL_COUPON_USD_4W"])
    assert len(selected) == 2 and sources == {"UST_REAL", "UST_BILL"}


def test_tips_parser_keeps_negative_real_rates():
    txt = "Date,5 YR,7 YR,10 YR,20 YR,30 YR\n01/02/2025,-0.13,0.40,1.60,2.00,N/A\n"
    rows = parse_real_csv(txt, date(2025, 1, 1), date(2025, 1, 31))
    assert len(rows) == 4
    assert next(r for r in rows if r.series_id == "UST_REAL_PAR_USD_5Y").rate == pytest.approx(
        -0.0013
    )
    assert all(r.bid is None and r.ask is None for r in rows)


def test_bill_duplicate_coupon_headers_by_position():
    header = ["Date"]
    record = ["10/01/2026"]
    for week in (4, 6, 8, 13, 17, 26, 52):
        header.extend([f"{week} WEEKS BANK DISCOUNT", "COUPON EQUIVALENT"])
        record.extend(["3.5", "3.6"])
    content = ",".join(header) + "\n" + ",".join(record) + "\n"
    rows = parse_bill_csv(content, date(2026, 10, 1), date(2026, 10, 31))
    assert len(rows) == 14
    assert sorted({round(r.rate, 6) for r in rows}) == [0.035, 0.036]
    assert {r.series_id for r in rows} == set(configured_series()) - {
        f"UST_REAL_PAR_USD_{t}Y" for t in (5, 7, 10, 20, 30)
    }


def test_invalid_bill_headers_fail_closed():
    with pytest.raises(ValueError, match="missing week maturities"):
        parse_bill_csv(
            "Date,4 WEEKS BANK DISCOUNT,COUPON EQUIVALENT\n2026-10-01,3.5,3.6\n",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )


def test_schema_has_nullable_quote_fields():
    assert any('"BID" FLOAT' in ddl and '"ASK" FLOAT' in ddl for ddl in SCHEMA_DDL)
    assert (
        QuoteableRateObservation("TEST", date(2026, 1, 1), 0.03, bid=0.029, ask=0.031).bid == 0.029
    )


def test_bill_tenor_qualified_coupon_headers_and_extra_columns():
    header = ["Date", "20 YR", "Extrapolation Factor"]
    record = ["11/10/2026", "N/A", "N/A"]
    for week in (4, 6, 8, 13, 17, 26, 52):
        header.extend([f"{week} WEEKS BANK DISCOUNT", "other", f"{week} WEEKS COUPON EQUIVALENT"])
        record.extend(["3.5", "N/A", "3.6"])
    text = ",".join(header) + "\n" + ",".join(record) + "\n"
    rows = parse_bill_csv(text, date(2026, 11, 1), date(2026, 11, 11))
    assert len(rows) == 14
    assert next(x.rate for x in rows if x.series_id == "UST_BILL_COUPON_USD_4W") == pytest.approx(
        0.036
    )


def test_bill_generic_coupon_headers_with_extra_columns():
    header = ["Date"]
    record = ["11/10/2026"]
    for week in (4, 6, 8, 13, 17, 26, 52):
        header.extend([f"{week} WEEKS BANK DISCOUNT", "EXTRA", "COUPON EQUIVALENT"])
        record.extend(["3.5", "N/A", "3.6"])
    text = ",".join(header) + "\n" + ",".join(record) + "\n"
    assert len(parse_bill_csv(text, date(2026, 11, 1), date(2026, 11, 11))) == 14
