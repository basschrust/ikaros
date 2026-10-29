from datetime import date
from ingestion.fw20_registry import standard_expiry, candidate_spec, matching


def test_four_series():
    assert [standard_expiry(x) for x in ("FW20Z2620", "FW20H2720", "FW20M2720", "FW20U2720")] == [
        date(2026, 12, 18),
        date(2027, 3, 19),
        date(2027, 6, 18),
        date(2027, 9, 17),
    ]


def test_bad_symbols():
    for sym in ["FW20H27", "FW20F2720", "FW40H27", "FW20H272000"]:
        assert standard_expiry(sym) is None


def test_discovery_thursday_is_acceptable_but_stored_as_standard_friday():
    r = candidate_spec("FW20H2720", date(2027, 3, 18), as_of=date(2026, 10, 29))
    assert r["expiry_date"] == date(2027, 3, 19)
    assert r["multiplier"] == 20.0


def test_reject_expired_or_wrong_month():
    assert candidate_spec("FW20Z2620", date(2026, 12, 17), as_of=date(2026, 12, 19)) is None
    assert candidate_spec("FW20Z2620", date(2027, 1, 17), as_of=date(2026, 10, 29)) is None


def test_matching_old_source():
    spec = candidate_spec("FW20Z2620", date(2026, 12, 17), as_of=date(2026, 10, 29))
    assert matching(
        ("FW20Z2620", "FUTURE", "WIG20", date(2026, 12, 18), "PLN", 20.0, None, None), spec
    )
    assert not matching(
        ("FW20Z2620", "FUTURE", "WIG20", date(2026, 12, 17), "PLN", 20.0, None, None), spec
    )
