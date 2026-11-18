"""Reconcile GPW candidates to the single authoritative PRICES table.

Only this module writes PRICES for the new multi-source job. Use one instance
of this writer at a time; standard Snowflake primary keys are not enforced.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from ingestion.decimal_prices import decimal_text, integer_text
from ingestion.gpw_provider_feeds import RawCandle

PROVIDER_CODES = frozenset({"YFINANCE", "STOOQ", "BIZNESRADAR"})


@dataclass(frozen=True)
class Mapping:
    asset_id: int
    canonical_symbol: str
    provider_code: str
    provider_id: int
    provider_symbol_id: int
    provider_symbol: str
    priority: int
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class Candidate:
    mapping: Mapping
    candle: RawCandle
    candidate_id: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class Selection:
    candidate: Candidate | None
    reason: str
    confirmed_by: tuple[str, ...]


def _read_date(item: object) -> date:
    if isinstance(item, datetime):
        return item.date()
    if isinstance(item, date):
        return item
    return date.fromisoformat(str(item)[:10])


def load_mappings(
    connection: Any, *, symbols: Sequence[str] | None = None
) -> dict[int, list[Mapping]]:
    """Load active GPW equities and mappings. Explicit selection must be complete."""
    sql = """SELECT s."ASSET_ID", s."SYMBOL", p."PROVIDER_CODE", p."PROVIDER_ID",
                    ps."PROVIDER_SYMBOL_ID", ps."PROVIDER_SYMBOL", p."PRIORITY",
                    ps."VALID_FROM", ps."VALID_TO"
             FROM "SECURITIES" s JOIN "PROVIDER_SYMBOLS" ps ON ps."ASSET_ID"=s."ASSET_ID"
             JOIN "PROVIDERS" p ON p."PROVIDER_ID"=ps."PROVIDER_ID"
             WHERE s."VENUE"='GPW' AND LOWER(s."INSTRUMENT_CLASS") IN ('stock','equity')
               AND COALESCE(s."IS_ACTIVE", TRUE)=TRUE AND p."IS_ACTIVE"=TRUE
               AND ps."IS_ACTIVE"=TRUE"""
    with closing(connection.cursor()) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    wanted = set(symbols) if symbols is not None else None
    by_asset: dict[int, list[Mapping]] = defaultdict(list)
    found_symbols: set[str] = set()
    for asset_id, canonical, code, pid, psid, source_symbol, rank, start, end in rows:
        if wanted is not None and str(canonical) not in wanted:
            continue
        if code not in PROVIDER_CODES:
            continue
        found_symbols.add(str(canonical))
        by_asset[int(asset_id)].append(
            Mapping(
                int(asset_id),
                str(canonical),
                str(code),
                int(pid),
                int(psid),
                str(source_symbol),
                int(rank),
                _read_date(start),
                _read_date(end) if end is not None else None,
            )
        )
    if wanted is not None and found_symbols != wanted:
        raise ValueError(f"Missing active GPW price mappings: {sorted(wanted - found_symbols)}")
    for asset_id, group in by_asset.items():
        if len({m.canonical_symbol for m in group}) != 1:
            raise ValueError(f"Inconsistent canonical symbol for asset {asset_id}")
        for code in {m.provider_code for m in group}:
            matches = [m for m in group if m.provider_code == code]
            for i, left in enumerate(matches):
                for right in matches[i + 1 :]:
                    if left.valid_from <= (right.valid_to or date.max) and right.valid_from <= (
                        left.valid_to or date.max
                    ):
                        raise ValueError(f"Overlapping active mappings for {asset_id} {code}")
    return dict(by_asset)


def _valid(candle: RawCandle, *, positive_volume: bool) -> tuple[str, str | None]:
    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(x is None or not x.is_finite() or x <= 0 for x in prices):
        return "REJECTED", "MISSING_OR_NONPOSITIVE_OHLC"
    assert candle.open is not None and candle.high is not None
    assert candle.low is not None and candle.close is not None
    if (
        candle.low > min(candle.open, candle.close)
        or candle.high < max(candle.open, candle.close)
        or candle.low > candle.high
    ):
        return "REJECTED", "INCONSISTENT_OHLC"
    if candle.volume is None or candle.volume < 0:
        return "REJECTED", "MISSING_OR_NEGATIVE_VOLUME"
    if positive_volume and candle.volume == 0:
        return "REJECTED", "ZERO_VOLUME_LIQUID_EQUITY"
    try:
        for value in prices:
            decimal_text(value, allow_null=False)
        integer_text(candle.volume, allow_null=False)
    except (ValueError, ArithmeticError):
        return "REJECTED", "OUT_OF_RANGE_FIXED_POINT"
    return "VALID", None


def make_candidate(
    mapping: Mapping, candle: RawCandle, *, positive_volume: bool = True
) -> Candidate:
    status, reason = _valid(candle, positive_volume=positive_volume)

    # Stable across source formatting changes such as 75, 75.0 and 75.00.
    def canonical_number(value: Decimal | None) -> str:
        return "NULL" if value is None else format(value.normalize(), "f")

    payload = "|".join(
        map(
            str,
            (
                mapping.asset_id,
                mapping.provider_id,
                mapping.provider_symbol_id,
                candle.day.isoformat(),
                canonical_number(candle.open),
                canonical_number(candle.high),
                canonical_number(candle.low),
                canonical_number(candle.close),
                candle.volume,
            ),
        )
    )
    return Candidate(
        mapping, candle, hashlib.sha256(payload.encode("utf-8")).hexdigest(), status, reason
    )


def _near(a: Decimal, b: Decimal, *, tolerance: Decimal) -> bool:
    return abs(a - b) <= max(abs(a), abs(b)) * tolerance


def _agree_ohlc(a: RawCandle, b: RawCandle) -> bool:
    """Verify two independent providers observed the same price envelope.

    Volume is intentionally excluded: a ZERO_VOLUME candidate must never
    contribute a volume, but its prices can corroborate a complete candle.
    """
    if a.day != b.day:
        return False
    for attribute in ("open", "high", "low", "close"):
        aa, bb = getattr(a, attribute), getattr(b, attribute)
        if aa is None or bb is None or not _near(aa, bb, tolerance=Decimal("0.005")):
            return False
    return True


def _agree(a: RawCandle, b: RawCandle) -> bool:
    if not _agree_ohlc(a, b):
        return False
    if a.volume is None or b.volume is None or a.volume <= 0 or b.volume <= 0:
        return False
    return _near(Decimal(a.volume), Decimal(b.volume), tolerance=Decimal("0.15"))


def choose(
    candidates: Sequence[Candidate],
    *,
    minimum_confirmations: int = 1,
    allow_one_volume_source: bool = False,
) -> Selection:
    """Select an individually valid, whole-source OHLCV candle.

    Default: choose the highest-priority VALID provider, even without a second
    feed. Disagreements are retained as evidence, not used as a veto. Callers
    may explicitly request legacy multi-source consensus (2 or 3 sources).
    Never combine price or volume fields from different providers.
    """
    if minimum_confirmations not in (1, 2, 3):
        raise ValueError("minimum_confirmations must be 1, 2 or 3")
    valid = [c for c in candidates if c.status == "VALID"]
    if minimum_confirmations == 1:
        if not valid:
            return Selection(None, "NO_VALID_SOURCE", ())
        selected = min(
            valid, key=lambda c: (c.mapping.priority, c.mapping.provider_code, c.candidate_id)
        )
        agreeing = tuple(
            sorted({c.mapping.provider_code for c in valid if _agree(selected.candle, c.candle)})
        )
        disagreement = any(not _agree(selected.candle, c.candle) for c in valid)
        reason = (
            "PRIORITY_SELECTED_DISAGREEMENT"
            if disagreement
            else "MULTI_SOURCE_AGREEMENT"
            if len(agreeing) > 1
            else "SINGLE_VALID_SOURCE"
        )
        return Selection(selected, reason, agreeing)
    if len({c.mapping.provider_code for c in valid}) < minimum_confirmations:
        # Explicitly opt-in only: one VALID full OHLCV plus one independent
        # zero-volume candle whose OHLC agrees. Never use the zero-volume
        # candle for authoritative data, only as price corroboration.
        if allow_one_volume_source and minimum_confirmations == 2 and len(valid) == 1:
            reference = valid[0]
            corroborators = [
                c
                for c in candidates
                if c.mapping.provider_code != reference.mapping.provider_code
                and c.status == "REJECTED"
                and c.reason == "ZERO_VOLUME_LIQUID_EQUITY"
                and c.candle.volume == 0
                and _agree_ohlc(c.candle, reference.candle)
            ]
            if corroborators:
                confirmed = tuple(
                    sorted(
                        {reference.mapping.provider_code}
                        | {c.mapping.provider_code for c in corroborators}
                    )
                )
                return Selection(reference, "PRICE_CONFIRMED_VOLUME_SINGLE_SOURCE", confirmed)
        return Selection(None, "INSUFFICIENT_VALID_SOURCES", ())
    groups: list[tuple[Candidate, ...]] = []
    for candidate in valid:
        group = tuple(other for other in valid if _agree(candidate.candle, other.candle))
        if len({m.mapping.provider_code for m in group}) >= minimum_confirmations:
            groups.append(group)
    if not groups:
        return Selection(None, "CROSS_PROVIDER_CONFLICT", ())
    # Every member of the chosen cluster must agree with every other member.
    groups = [
        group for group in groups if all(_agree(a.candle, b.candle) for a in group for b in group)
    ]
    if not groups:
        return Selection(None, "NONTRANSITIVE_AGREEMENT", ())
    maximal = max(len({c.mapping.provider_code for c in group}) for group in groups)
    best = {
        tuple(sorted(c.candidate_id for c in group)): group
        for group in groups
        if len({c.mapping.provider_code for c in group}) == maximal
    }
    if len(best) != 1:
        return Selection(None, "AMBIGUOUS_CONSENSUS", ())
    cluster = next(iter(best.values()))
    selected = min(cluster, key=lambda c: (c.mapping.priority, c.mapping.provider_code))
    return Selection(selected, "CONSENSUS", tuple(sorted(c.mapping.provider_code for c in cluster)))


def _candidate_params(candidate: Candidate) -> tuple[object, ...]:
    c = candidate.candle
    return (
        candidate.candidate_id,
        candidate.mapping.asset_id,
        candidate.mapping.provider_id,
        candidate.mapping.provider_symbol_id,
        candidate.mapping.provider_symbol,
        c.day.isoformat(),
        decimal_text(c.open),
        decimal_text(c.high),
        decimal_text(c.low),
        decimal_text(c.close, allow_null=False),
        integer_text(c.volume),
        candidate.status,
        candidate.reason,
    )


def persist_candidates(connection: Any, candidates: Sequence[Candidate]) -> int:
    """Idempotent append-only observations. Call inside the caller's transaction."""
    created = 0
    sql = """INSERT INTO "PRICES_CANDIDATES"
        ("CANDIDATE_ID","ASSET_ID","PROVIDER_ID","PROVIDER_SYMBOL_ID","SYMBOL","DATE",
        "OPEN","HIGH","LOW","CLOSE","VOLUME","VALIDATION_STATUS","VALIDATION_REASON")
        VALUES (%s,%s,%s,%s,%s,TO_DATE(%s,'YYYY-MM-DD'),
        TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,24,8),
        TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,38,0),%s,%s)"""
    with closing(connection.cursor()) as cur:
        for candidate in candidates:
            if candidate.candle.close is None:
                # The schema requires CLOSE NOT NULL: record this in the job logs.
                continue
            cur.execute(
                'SELECT 1 FROM "PRICES_CANDIDATES" WHERE "CANDIDATE_ID"=%s',
                (candidate.candidate_id,),
            )
            if cur.fetchall():
                continue
            cur.execute(sql, _candidate_params(candidate))
            created += 1
    return created


def _is_same(existing: tuple[object, ...], candle: RawCandle) -> bool:
    # existing: symbol, open, high, low, close, volume, candidate_id
    for i, field in enumerate(("open", "high", "low", "close"), start=1):
        a, b = existing[i], getattr(candle, field)
        if (a is None) != (b is None):
            return False
        if a is not None and Decimal(str(a)) != b:
            return False
    return (existing[5] is None and candle.volume is None) or (
        existing[5] is not None
        and candle.volume is not None
        and Decimal(str(existing[5])) == Decimal(candle.volume)
    )


def promote(connection: Any, selection: Selection, *, canonical_symbol: str) -> str:
    """Promote missing, identical or demonstrably suspect rows; preserve valid conflicts.

    Assumes a single writer and an open transaction. Duplicate existing rows
    trigger a hard failure rather than an ambiguous update.
    """
    if selection.candidate is None:
        return "UNRESOLVED"
    item = selection.candidate
    candle, mapping = item.candle, item.mapping
    with closing(connection.cursor()) as cur:
        cur.execute(
            """SELECT "SYMBOL","OPEN","HIGH","LOW","CLOSE","VOLUME","CANDIDATE_ID"
               FROM "PRICES" WHERE TO_DATE("DATE")=TO_DATE(%s,'YYYY-MM-DD')
               AND ("ASSET_ID"=%s OR ("ASSET_ID" IS NULL AND "SYMBOL"=%s))""",
            (candle.day.isoformat(), mapping.asset_id, canonical_symbol),
        )
        rows = cur.fetchall()
        if len(rows) > 1:
            raise ValueError(
                f"Duplicate authoritative PRICES rows for {canonical_symbol} {candle.day}"
            )
        fields = (
            decimal_text(candle.open),
            decimal_text(candle.high),
            decimal_text(candle.low),
            decimal_text(candle.close, allow_null=False),
            integer_text(candle.volume),
        )
        if not rows:
            cur.execute(
                """INSERT INTO "PRICES" ("SYMBOL","DATE","ASSET_ID","OPEN","HIGH","LOW",
                   "CLOSE","VOLUME","PROVIDER_ID","CANDIDATE_ID","SELECTED_AT","SELECTION_REASON")
                   VALUES (%s,TO_TIMESTAMP_NTZ(%s,'YYYY-MM-DD'),%s,
                   TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,24,8),
                   TO_DECIMAL(%s,24,8),TO_DECIMAL(%s,38,0),%s,%s,CURRENT_TIMESTAMP(),%s)""",
                (
                    canonical_symbol,
                    candle.day.isoformat(),
                    mapping.asset_id,
                    *fields,
                    mapping.provider_id,
                    item.candidate_id,
                    selection.reason,
                ),
            )
            return "INSERTED"
        existing = rows[0]
        if existing[6] == item.candidate_id:
            return "UNCHANGED"
        same = _is_same(existing, candle)
        is_suspect = (
            existing[5] is None
            or Decimal(str(existing[5])) == 0
            or any(value is None for value in existing[1:5])
        )
        if not same and not is_suspect:
            return "PRESERVED_EXISTING_CONFLICT"
        cur.execute(
            """UPDATE "PRICES" SET "ASSET_ID"=%s,"PROVIDER_ID"=%s,"CANDIDATE_ID"=%s,
               "SELECTED_AT"=CURRENT_TIMESTAMP(),"SELECTION_REASON"=%s,
               "OPEN"=TO_DECIMAL(%s,24,8),"HIGH"=TO_DECIMAL(%s,24,8),
               "LOW"=TO_DECIMAL(%s,24,8),"CLOSE"=TO_DECIMAL(%s,24,8),
               "VOLUME"=TO_DECIMAL(%s,38,0)
               WHERE TO_DATE("DATE")=TO_DATE(%s,'YYYY-MM-DD')
               AND ("ASSET_ID"=%s OR ("ASSET_ID" IS NULL AND "SYMBOL"=%s))""",
            (
                mapping.asset_id,
                mapping.provider_id,
                item.candidate_id,
                "MATCHED_EXISTING" if same else "REPAIRED_SUSPECT",
                *fields,
                candle.day.isoformat(),
                mapping.asset_id,
                canonical_symbol,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Unexpected PRICES update count; transaction must roll back")
        return "MATCHED_EXISTING" if same else "REPAIRED_SUSPECT"


def commit_day(
    connection: Any,
    candidates: Sequence[Candidate],
    selection: Selection,
    *,
    canonical_symbol: str,
    write: bool,
) -> tuple[int, str]:
    if not write:
        return 0, "DRY_RUN_" + (selection.reason if selection.candidate is None else "PROMOTION")
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            inserted = persist_candidates(connection, candidates)
            outcome = promote(connection, selection, canonical_symbol=canonical_symbol)
            cur.execute("COMMIT")
            return inserted, outcome
        except Exception:
            cur.execute("ROLLBACK")
            raise
