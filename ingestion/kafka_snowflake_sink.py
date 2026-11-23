"""Kafka observation persistence interface: candidate-only, no final DML.

The existing public names remain stable for run.kafka_sink and unit tests.
Independent promotion belongs to run.promote_candidates.
"""

from __future__ import annotations

from database.kafka_candidate_sink import pair_id, upsert_observation

__all__ = ["pair_id", "upsert_observation"]
