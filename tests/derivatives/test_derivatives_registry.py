from datetime import date

from ingestion.derivatives_registry import review, third_friday


def test_index_with_suffix():
    item = review("FW20Z2620", date(2026, 12, 17))
    assert item.family == "FW20"
    assert item.category == "INDEX"
    assert item.underlying_hint == "WIG20"
    assert item.contract_month == "2026-12"
    assert item.third_friday == date(2026, 12, 18)
    assert not item.expiry_consistent


def test_index_without_suffix():
    item = review("FW40H27", date(2027, 3, 18))
    assert item.underlying_hint == "mWIG40"


def test_currency_monthly_and_quarterly():
    assert review("FUSDX26", None).contract_month == "2026-11"
    assert review("FUSDF27", None).contract_month == "2027-01"
    assert review("FUSDH27", None).category == "CURRENCY"


def test_stock_candidate_only():
    item = review("FCDRZ26", date(2026, 12, 17))
    assert item.category == "EQUITY_CANDIDATE"
    assert item.underlying_hint == "CDR.WA"
    assert item.status == "REVIEW"


def test_invalid_symbol_and_suffix():
    assert review("garbage", None).category == "UNKNOWN"
    assert "Unexpected" in review("FUSDZ2620", None).reason


def test_third_friday():
    assert third_friday(2027, 6) == date(2027, 6, 18)
