"""Kafka observation persistence interface.

The existing public functions are retained for callers and tests. Backend SQL
belongs exclusively to database.kafka_sink_queries.
"""

from __future__ import annotations

from database.kafka_sink_queries import pair_id, upsert_observation

__all__ = ["pair_id", "upsert_observation"]
