"""Kafka must durably stage evidence before acknowledging consumer offsets."""

from __future__ import annotations

from datetime import date

import pytest

from database import kafka_candidate_sink as sink
from ingestion.kafka_inbound import Observation


def event(dataset="FX", **kwargs):
    payload = dict(
        dataset=dataset,
        symbol="EURPLN=X" if dataset == "FX" else "PZU.WA",
        day=date(2026, 11, 18),
        open=4.20 if dataset == "FX" else 61.0,
        high=4.25 if dataset == "FX" else 62.0,
        low=4.19 if dataset == "FX" else 60.0,
        close=4.23 if dataset == "FX" else 61.5,
        volume=0.0 if dataset == "FX" else 1234.0,
        provider="yfinance",
    )
    payload.update(kwargs)
    return Observation(**payload)


def test_fx_event_stages_candidates_only(monkeypatch):
    stored = []
    monkeypatch.setattr(sink, "pair_id", lambda c, s: 18)
    monkeypatch.setattr(
        sink,
        "stage_fx",
        lambda _conn, observations, **kwargs: (stored.extend(observations) or 1, 1),
    )
    sink.upsert_observation(object(), event())
    assert len(stored) == 1
    assert stored[0].ref_id == 18
    assert stored[0].quote_kind == "MARKET"
    assert stored[0].volume == 0


def test_gpw_staging_rejects_unmapped_providers(monkeypatch):
    monkeypatch.setattr(sink, "load_mappings", lambda *_a, **_k: {101: []})
    with pytest.raises(ValueError, match="Expected one active"):
        sink.upsert_observation(object(), event("GPW"))


def test_gpw_dividend_cannot_be_discarded():
    with pytest.raises(ValueError, match="dividend"):
        sink.upsert_observation(object(), event("GPW", dividend=2.0))


def test_fractional_volume_rejected():
    with pytest.raises(ValueError, match="volume"):
        sink.upsert_observation(object(), event("GPW", volume=12.5))
