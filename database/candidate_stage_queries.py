"""Transactional candidate-only writes for existing Ikaros price domains."""

from __future__ import annotations
from contextlib import closing
from typing import Any, Sequence
from database.gpw_price_pipeline import Candidate as EquityCandidate, persist_candidates
from database.market_quote_pipeline import Candidate as MarketCandidate, save_candidate


def store_equity_candidates(
    connection: Any, candidates: Sequence[EquityCandidate], *, backend: str
) -> int:
    """Commit equity evidence without touching PRICES."""
    if not candidates:
        return 0
    if backend != "snowflake":
        raise ValueError(
            "Existing equity candidate writer currently requires Snowflake SQL bindings"
        )
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
    """Commit WIG20/contract evidence; never call promote()."""
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            _identifier, created = save_candidate(connection, kind, candidate, backend)
            cur.execute("COMMIT")
            return created
        except Exception:
            cur.execute("ROLLBACK")
            raise
