"""Unit tests: a bad volume cannot become authoritative through agreement."""

from datetime import date
from decimal import Decimal

from database.gpw_price_pipeline import Mapping, choose, make_candidate
from ingestion.gpw_provider_feeds import RawCandle

DAY = date(2026, 11, 17)


def candidate(provider: str, pid: int, volume: int, *, close: str = "75.00"):
    mapping = Mapping(101, "PZU.WA", provider, pid, pid, "PZU", pid * 10, date(1900, 1, 1), None)
    candle = RawCandle(
        DAY, Decimal("75.96"), Decimal("75.96"), Decimal("74.78"), Decimal(close), volume
    )
    return make_candidate(mapping, candle)


def test_single_volume_opt_in_returns_full_valid_candle_not_zero_volume():
    yahoo = candidate("YFINANCE", 1, 0)
    radar = candidate("BIZNESRADAR", 3, 1_248_008)
    assert yahoo.status == "REJECTED"
    assert yahoo.reason == "ZERO_VOLUME_LIQUID_EQUITY"
    assert choose([yahoo, radar]).candidate is radar
    strict = choose([yahoo, radar], minimum_confirmations=2)
    assert strict.candidate is None
    selection = choose([yahoo, radar], minimum_confirmations=2, allow_one_volume_source=True)
    assert selection.reason == "PRICE_CONFIRMED_VOLUME_SINGLE_SOURCE"
    assert selection.candidate is radar
    assert selection.confirmed_by == ("BIZNESRADAR", "YFINANCE")


def test_disabled_when_requesting_three_full_sources():
    yahoo = candidate("YFINANCE", 1, 0)
    radar = candidate("BIZNESRADAR", 3, 1_248_008)
    assert (
        choose([yahoo, radar], minimum_confirmations=3, allow_one_volume_source=True).candidate
        is None
    )


def test_prices_disagree():
    yahoo = candidate("YFINANCE", 1, 0, close="75.00")
    radar = candidate("BIZNESRADAR", 3, 1_248_008, close="75.60")
    assert (
        choose([yahoo, radar], minimum_confirmations=2, allow_one_volume_source=True).candidate
        is None
    )


def test_two_invalid_volumes():
    yahoo = candidate("YFINANCE", 1, 0)
    radar = candidate("BIZNESRADAR", 3, 0)
    assert (
        choose([yahoo, radar], minimum_confirmations=2, allow_one_volume_source=True).candidate
        is None
    )


def test_valid_volume_disagreement_is_logged_but_not_vetoed_by_default():
    yahoo = candidate("YFINANCE", 1, 0)
    radar = candidate("BIZNESRADAR", 3, 1_248_008)
    stooq = candidate("STOOQ", 2, 100_000)
    assert choose([yahoo, radar, stooq]).candidate is stooq
    assert (
        choose(
            [yahoo, radar, stooq], minimum_confirmations=2, allow_one_volume_source=True
        ).candidate
        is None
    )


def test_all_valid_sources_agree_and_priority_wins():
    yahoo = candidate("YFINANCE", 1, 1_248_008)
    radar = candidate("BIZNESRADAR", 3, 1_248_008)
    decision = choose([yahoo, radar], allow_one_volume_source=True)
    assert decision.candidate is yahoo
    assert decision.reason == "MULTI_SOURCE_AGREEMENT"
