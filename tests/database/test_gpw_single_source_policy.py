"""Regression tests for GPW default: one trustworthy, fully validated candle suffices."""

from datetime import date
from decimal import Decimal

from database.gpw_price_pipeline import Mapping, choose, make_candidate
from ingestion.gpw_provider_feeds import RawCandle


def _item(provider: str, rank: int, day: date, prices: tuple[str, str, str, str], volume: int):
    mapping = Mapping(
        asset_id=1001,
        canonical_symbol="PZU.WA",
        provider_code=provider,
        provider_id=rank,
        provider_symbol_id=rank,
        provider_symbol="PZU.WA" if provider == "YFINANCE" else "PZU",
        priority=rank * 10,
        valid_from=date(2020, 1, 1),
        valid_to=None,
    )
    return make_candidate(mapping, RawCandle(day, *(Decimal(x) for x in prices), volume))


def test_november_16_pzu_realistic_bad_yahoo_falls_back_to_radar():
    day = date(2026, 11, 16)
    yahoo = _item("YFINANCE", 1, day, ("73.68000031",) * 4, 0)
    radar = _item("BIZNESRADAR", 3, day, ("75.60", "76.24", "75.16", "75.96"), 1_063_267)
    decision = choose([yahoo, radar])
    assert yahoo.status == "REJECTED"
    assert yahoo.reason == "ZERO_VOLUME_LIQUID_EQUITY"
    assert radar.status == "VALID"
    assert decision.candidate is radar
    assert decision.reason == "SINGLE_VALID_SOURCE"
    assert decision.confirmed_by == ("BIZNESRADAR",)
    assert decision.candidate.candle.volume == 1_063_267
    assert choose([yahoo, radar], minimum_confirmations=2).candidate is None


def test_november_17_pzu_is_not_blocked_by_price_disagreement_with_invalid_source():
    day = date(2026, 11, 17)
    yahoo = _item("YFINANCE", 1, day, ("73.68000031",) * 4, 0)
    radar = _item("BIZNESRADAR", 3, day, ("75.96", "75.96", "74.78", "75.00"), 1_248_008)
    decision = choose([yahoo, radar])
    assert decision.candidate is radar
    assert decision.candidate.candle.close == Decimal("75.00")
    assert decision.candidate.candle.volume == 1_248_008


def test_no_valid_provider_never_promotes():
    day = date(2026, 11, 17)
    yahoo = _item("YFINANCE", 1, day, ("73.68",) * 4, 0)
    radar = _item("BIZNESRADAR", 3, day, ("75.00",) * 4, 0)
    selection = choose([yahoo, radar])
    assert selection.candidate is None
    assert selection.reason == "NO_VALID_SOURCE"


def test_conflicting_valid_candles_choose_ranked_source_without_field_mixing():
    day = date(2026, 11, 17)
    yahoo = _item("YFINANCE", 1, day, ("74.1", "74.9", "73.8", "74.6"), 100_000)
    radar = _item("BIZNESRADAR", 3, day, ("75.96", "75.96", "74.78", "75.00"), 1_248_008)
    selection = choose([yahoo, radar])
    assert selection.candidate is yahoo
    assert selection.reason == "PRIORITY_SELECTED_DISAGREEMENT"
    assert selection.candidate.candle.volume == 100_000
    assert choose([yahoo, radar], minimum_confirmations=2).candidate is None
