"""Transactional candidate-only storage for equity, WIG20 and derivatives."""

from __future__ import annotations
from contextlib import closing
from typing import Any, Sequence
from database.gpw_price_pipeline import Candidate as EquityCandidate, persist_candidates
from database.market_quote_pipeline import Candidate as MarketCandidate, save_candidate


def store_equity_candidates(
    connection: Any, candidates: Sequence[EquityCandidate], *, backend: str
) -> int:
    """Append evidence without updating PRICES, on both supported backends."""
    if not candidates:
        return 0
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Unsupported database backend {backend!r}")
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            count, _ = persist_candidates(connection, candidates)
            cur.execute("COMMIT")
            return count
        except Exception:
            cur.execute("ROLLBACK")
            raise


def store_market_candidate(
    connection: Any, *, kind: str, candidate: MarketCandidate, backend: str
) -> bool:
    """Commit market candidates; leave selection and final prices to promoter."""
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            _identifier, created = save_candidate(connection, kind, candidate, backend)
            cur.execute("COMMIT")
            return created
        except Exception:
            cur.execute("ROLLBACK")
            raise
