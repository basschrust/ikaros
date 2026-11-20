"""Kafka -> candidate-only durable storage. Never DML to final price tables.

Consumer offsets can be committed only after this stage finishes successfully.
A separate run.promote_candidates invocation publishes authoritative prices.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from database.candidate_catalog import fx_pairs
from database.candidate_observations import Observation as QuoteObservation
from database.candidate_observations import stage as stage_fx
from database.candidate_stage_queries import store_equity_candidates
from database.gpw_price_pipeline import load_mappings, make_candidate
from ingestion.gpw_provider_feeds import RawCandle
from ingestion.kafka_inbound import Observation


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _integer_volume(value: float | None) -> int | None:
    if value is None:
        return None
    number = Decimal(str(value))
    if number < 0 or not number.is_finite() or number != number.to_integral_value():
        raise ValueError("Kafka observation contains invalid non-integral trading volume")
    return int(number)


def pair_id(connection: Any, symbol: str) -> int:
    return fx_pairs(connection, symbols=[symbol])[symbol]


def upsert_observation(connection: Any, event: Observation) -> None:
    """Compatibility name: STAGE only, never upsert an authoritative price."""
    source = event.provider.strip().upper()
    if event.dataset == "FX":
        quote_kind = "REFERENCE" if source == "NBP" else "MARKET"
        if quote_kind == "REFERENCE" and any(
            value is not None for value in (event.open, event.high, event.low, event.volume)
        ):
            raise ValueError("NBP reference quote must be close-only")
        candidate = QuoteObservation(
            kind="FX",
            symbol=event.symbol,
            day=event.day,
            source=source,
            ref_id=pair_id(connection, event.symbol),
            quote_kind=quote_kind,
            open=_decimal(event.open),
            high=_decimal(event.high),
            low=_decimal(event.low),
            close=_decimal(event.close),
            volume=_integer_volume(event.volume),
        )
        received, _inserted = stage_fx(connection, [candidate], backend="snowflake", write=True)
        if received != 1:
            raise RuntimeError("Kafka FX observation was not staged")
        return

    if event.dataset == "GPW":
        # The equity candidate structure currently has no corporate action fields.
        # Do not silently discard a nonzero dividend or a split event.
        if event.dividend not in (None, 0, 0.0):
            raise ValueError("GPW event contains a dividend; candidate schema must be extended")
        if event.split_ratio not in (None, 0, 0.0, 1, 1.0):
            raise ValueError("GPW event contains a stock split; candidate schema must be extended")
        volume = _integer_volume(event.volume)
        catalog = load_mappings(connection, symbols=[event.symbol])
        eligible = [
            mapping
            for mappings in catalog.values()
            for mapping in mappings
            if mapping.provider_code == source
            and mapping.valid_from <= event.day
            and (mapping.valid_to is None or event.day <= mapping.valid_to)
        ]
        if len(eligible) != 1:
            raise ValueError(
                f"Expected one active {source} mapping for {event.symbol} on {event.day}, "
                f"found {len(eligible)}"
            )
        candle = RawCandle(
            event.day,
            _decimal(event.open),
            _decimal(event.high),
            _decimal(event.low),
            _decimal(event.close),
            volume,
        )
        store_equity_candidates(
            connection, [make_candidate(eligible[0], candle)], backend="snowflake"
        )
        return
    raise ValueError(f"Unsupported Kafka dataset {event.dataset!r}")
