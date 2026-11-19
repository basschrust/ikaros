"""Selection and content fingerprints independent of integer surrogate PKs."""

from datetime import date
from decimal import Decimal

from database.gpw_price_pipeline import Mapping, choose, make_candidate
from ingestion.gpw_provider_feeds import RawCandle

DAY = date(2026, 11, 17)


def mapping(provider: str, number: int) -> Mapping:
    return Mapping(
        101,
        "PZU.WA",
        provider,
        number,
        number,
        "PZU" if provider != "STOOQ" else "pzu",
        number * 10,
        date(1900, 1, 1),
        None,
    )


def candle(volume: int, close: str = "75.00") -> RawCandle:
    return RawCandle(
        DAY, Decimal("75.96"), Decimal("75.96"), Decimal("74.78"), Decimal(close), volume
    )


def test_zero_yahoo_volume_and_other_sources_confirm():
    candidates = [
        make_candidate(mapping("YFINANCE", 1), candle(0)),
        make_candidate(mapping("STOOQ", 2), candle(1247844)),
        make_candidate(mapping("BIZNESRADAR", 3), candle(1247844)),
    ]
    assert candidates[0].status == "REJECTED"
    decision = choose(candidates)
    assert decision.candidate is candidates[1]
    assert decision.confirmed_by == ("BIZNESRADAR", "STOOQ")


def test_provider_agreement_prefers_primary():
    group = [
        make_candidate(mapping(code, i), candle(1247844))
        for i, code in enumerate(("YFINANCE", "STOOQ", "BIZNESRADAR"), 1)
    ]
    assert choose(group).candidate is group[0]


def test_disagreement_falls_back_to_priority():
    group = [
        make_candidate(mapping("YFINANCE", 1), candle(1234567)),
        make_candidate(mapping("STOOQ", 2), candle(2100000)),
        make_candidate(mapping("BIZNESRADAR", 3), candle(800000)),
    ]
    result = choose(group)
    assert result.candidate is group[0]
    assert result.reason == "PRIORITY_SELECTED_DISAGREEMENT"
    strict = choose(group, minimum_confirmations=2)
    assert strict.candidate is None and strict.reason == "CROSS_PROVIDER_CONFLICT"


def test_candle_fields_are_never_blended():
    group = [
        make_candidate(mapping("STOOQ", 2), candle(1247844)),
        make_candidate(mapping("BIZNESRADAR", 3), candle(1247844, "75.10")),
    ]
    assert choose(group).candidate.candle.close == Decimal("75.00")


def test_content_hash_is_stable_and_source_sensitive():
    m = mapping("STOOQ", 2)
    a = make_candidate(m, candle(123))
    assert a.content_hash == make_candidate(m, candle(123)).content_hash
    assert a.content_hash != make_candidate(m, candle(124)).content_hash
    assert len(a.content_hash) == 64


def test_content_hash_ignores_decimal_trailing_zeros():
    m = mapping("STOOQ", 2)
    a = RawCandle(DAY, Decimal("75"), Decimal("76"), Decimal("74"), Decimal("75"), 1000)
    b = RawCandle(DAY, Decimal("75.00"), Decimal("76.00"), Decimal("74.0"), Decimal("75.0"), 1000)
    assert make_candidate(m, a).content_hash == make_candidate(m, b).content_hash
