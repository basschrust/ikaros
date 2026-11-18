"""Independent WIG20/derivative candidates -> validated authoritative prices.

Never combine provider fields. Snowflake standard PK/UNIQUE are metadata-only:
run one writer per domain; future parallel scheduling requires serialization.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping as TypeMapping, Sequence

from ingestion.market_quote_feeds import MarketCandle, MarketFeed

LOG = logging.getLogger("ikaros.market_quotes")
KINDS = ("INDEX", "DERIVATIVE")


@dataclass(frozen=True)
class ProviderMapping:
    instrument: str
    provider_id: int
    provider_code: str
    provider_symbol_id: int
    provider_symbol: str
    priority: int
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class Candidate:
    mapping: ProviderMapping
    quote: MarketCandle
    content_hash: str
    validation_status: str
    validation_reason: str | None


@dataclass(frozen=True)
class Result:
    instruments: int
    candidates_added: int
    promoted: int
    identical: int
    conflicts: int
    rejected: int
    source_errors: tuple[str, ...]
    no_data: int
    sessions_seen: int
    selected_days: int


def _sql(connection: Any, query: str, values: Sequence[object] = ()) -> list[tuple]:
    with closing(connection.cursor()) as cur:
        cur.execute(query, tuple(values))
        if query.lstrip().upper().startswith("SELECT"):
            return list(cur.fetchall())
        return []


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_mappings(
    connection: Any, kind: str, *, symbols: Sequence[str] | None = None, backend: str = "snowflake"
) -> dict[str, list[ProviderMapping]]:
    if kind not in KINDS:
        raise ValueError(kind)
    table = f"{kind}_PROVIDER_SYMBOLS"
    key = "INDEX_SYMBOL" if kind == "INDEX" else "CONTRACT_ID"
    query = (
        f'SELECT m."{key}", m."PROVIDER_ID", p."PROVIDER_CODE", '
        'm."PROVIDER_SYMBOL_ID", m."PROVIDER_SYMBOL", p."PRIORITY", '
        'm."VALID_FROM", m."VALID_TO" '
        f'FROM "{table}" m JOIN "PROVIDERS" p '
        'ON p."PROVIDER_ID"=m."PROVIDER_ID" '
        'WHERE m."IS_ACTIVE"=TRUE AND p."IS_ACTIVE"=TRUE'
    )
    by_symbol: dict[str, list[ProviderMapping]] = defaultdict(list)
    wanted = set(symbols) if symbols is not None else None
    for symbol, pid, code, sid, alias, priority, first, last in _sql(connection, query):
        canonical = str(symbol)
        if wanted is not None and canonical not in wanted:
            continue
        by_symbol[canonical].append(
            ProviderMapping(
                canonical,
                int(pid),
                str(code),
                int(sid),
                str(alias),
                int(priority),
                _date(first),
                _date(last) if last else None,
            )
        )
    if wanted is not None and set(by_symbol) != wanted:
        raise ValueError(f"No active {kind} mappings: {sorted(wanted - set(by_symbol))}")
    for symbol, rows in by_symbol.items():
        for code in {m.provider_code for m in rows}:
            subset = sorted(
                (m for m in rows if m.provider_code == code), key=lambda m: m.valid_from
            )
            for prev, cur in zip(subset, subset[1:]):
                if prev.valid_to is None or prev.valid_to >= cur.valid_from:
                    raise ValueError(f"Overlapping mappings: {symbol} {code}")
    return dict(by_symbol)


def _normalized(value: Decimal | int | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def validate(kind: str, candle: MarketCandle) -> tuple[str, str | None]:
    values = (candle.open, candle.high, candle.low, candle.close)
    if any(value is None or not value.is_finite() or value <= 0 for value in values):
        return "REJECTED", "MISSING_INVALID_OHLC"
    assert all(v is not None for v in values)
    op, high, low, close = values
    if high < max(op, close, low) or low > min(op, close, high):
        return "REJECTED", "INCONSISTENT_OHLC"
    if candle.volume is not None and (not isinstance(candle.volume, int) or candle.volume < 0):
        return "REJECTED", "BAD_VOLUME"
    if kind == "DERIVATIVE" and candle.volume is None:
        return "REJECTED", "MISSING_VOLUME"
    if candle.open_interest is not None and candle.open_interest < 0:
        return "REJECTED", "NEGATIVE_OPEN_INTEREST"
    if candle.settlement is not None and (
        not candle.settlement.is_finite() or candle.settlement <= 0
    ):
        return "REJECTED", "INVALID_SETTLEMENT"
    try:
        for v in (*values, candle.settlement):
            if v is not None:
                # Fixed-point precision: max 16 whole and 8 decimal places.
                n = v.quantize(Decimal("0.00000001"))
                if n != v or abs(n) >= Decimal("10000000000000000"):
                    return "REJECTED", "UNREPRESENTABLE_DECIMAL"
    except (InvalidOperation, ArithmeticError):
        return "REJECTED", "UNREPRESENTABLE_DECIMAL"
    # WIG20 is an index level, not shares traded: missing volume is expected.
    return "VALID", None


def make_candidate(kind: str, mapping: ProviderMapping, candle: MarketCandle) -> Candidate:
    status, reason = validate(kind, candle)
    payload = "|".join(
        (
            kind,
            mapping.instrument,
            str(mapping.provider_id),
            str(mapping.provider_symbol_id),
            candle.day.isoformat(),
            *(
                _normalized(x)
                for x in (
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.settlement,
                    candle.open_interest,
                )
            ),
        )
    )
    return Candidate(mapping, candle, hashlib.sha256(payload.encode()).hexdigest(), status, reason)


def choose(kind: str, candidates: Sequence[Candidate]) -> tuple[Candidate | None, str]:
    """One valid complete observation suffices; other sources are diagnostics."""
    valid = [c for c in candidates if c.validation_status == "VALID"]
    if not valid:
        return None, "NO_VALID_SOURCE"
    # WIG20 Yahoo can have stale daily bars; prefer reviewed exchange index
    # history where available. Equity provider ranking remains unaffected.
    order = {"BIZNESRADAR": 0, "STOOQ": 1, "YFINANCE": 2} if kind == "INDEX" else {}
    winner = min(
        valid,
        key=lambda c: (
            order.get(c.mapping.provider_code, 2),
            c.mapping.priority,
            c.mapping.provider_code,
            c.content_hash,
        ),
    )
    disagree = any(
        any(
            getattr(c.quote, f) != getattr(winner.quote, f)
            for f in ("open", "high", "low", "close")
        )
        for c in valid
        if c is not winner
    )
    return winner, (
        "PRIORITY_SELECTED_DISAGREEMENT"
        if disagree
        else "MULTI_SOURCE_AGREEMENT"
        if len(valid) > 1
        else "SINGLE_VALID_SOURCE"
    )


def _candidate_table(kind: str) -> tuple[str, str, str]:
    if kind not in KINDS:
        raise ValueError(kind)
    key = "INDEX_SYMBOL" if kind == "INDEX" else "CONTRACT_ID"
    return f"{kind}_PRICE_CANDIDATES", f"{kind}_PRICES", key


def _bind(backend: str) -> str:
    return "%s" if backend == "snowflake" else "?"


def _storage_value(value: Decimal | int | None) -> str | None:
    """Safe candidate storage, including rejected over-precision observations."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        try:
            rounded = value.quantize(Decimal("0.00000001"))
        except (InvalidOperation, ArithmeticError):
            return None
        if abs(rounded) >= Decimal("10000000000000000"):
            return None
        return format(rounded, "f")
    return str(value)


def save_candidate(
    connection: Any, kind: str, candidate: Candidate, backend: str
) -> tuple[int, bool]:
    table, _, key = _candidate_table(kind)
    b = _bind(backend)
    seen = _sql(
        connection,
        f'SELECT "CANDIDATE_ID" FROM "{table}" WHERE "CONTENT_HASH"={b}',
        (candidate.content_hash,),
    )
    if len(seen) > 1:
        raise ValueError("Duplicate candidate CONTENT_HASH in database")
    if seen:
        return int(seen[0][0]), False
    seq = f"{table}_ID_SEQ"
    nextval = f'"{seq}".NEXTVAL' if backend == "snowflake" else f"nextval('{seq}')"
    fields = (
        "CONTENT_HASH",
        key,
        "PROVIDER_ID",
        "PROVIDER_SYMBOL_ID",
        "DATE",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "VOLUME",
        "SETTLEMENT",
        "OPEN_INTEREST",
        "VALIDATION_STATUS",
        "VALIDATION_REASON",
    )
    columns = ",".join(f'"{x}"' for x in fields)
    values = ",".join(b for _ in fields)
    q = candidate.quote
    params = (
        candidate.content_hash,
        candidate.mapping.instrument,
        candidate.mapping.provider_id,
        candidate.mapping.provider_symbol_id,
        q.day.isoformat(),
        *(
            _storage_value(x)
            for x in (q.open, q.high, q.low, q.close, q.volume, q.settlement, q.open_interest)
        ),
        candidate.validation_status,
        candidate.validation_reason,
    )
    _sql(
        connection,
        f'INSERT INTO "{table}" ("CANDIDATE_ID",{columns}) VALUES ({nextval},{values})',
        params,
    )
    inserted = _sql(
        connection,
        f'SELECT "CANDIDATE_ID" FROM "{table}" WHERE "CONTENT_HASH"={b}',
        (candidate.content_hash,),
    )
    if len(inserted) != 1:
        raise RuntimeError("Unable to find newly inserted candidate")
    return int(inserted[0][0]), True


def _same(a: object, b: Decimal | int | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.000001")


def promote(
    connection: Any, kind: str, candidate: Candidate, candidate_id: int, reason: str, backend: str
) -> str:
    _, table, key = _candidate_table(kind)
    b = _bind(backend)
    q = candidate.quote
    where = f'"{key}"={b} AND CAST("DATE" AS DATE)=CAST({b} AS DATE)'
    rows = _sql(
        connection,
        f'SELECT "OPEN","HIGH","LOW","CLOSE",'
        f"{'NULL AS VOLUME' if kind == 'INDEX' else chr(34) + 'VOLUME' + chr(34)},"
        f'"SOURCE","CANDIDATE_ID" FROM "{table}" WHERE {where}',
        (candidate.mapping.instrument, q.day.isoformat()),
    )
    if len(rows) > 1:
        raise ValueError(f"Duplicate authoritative {kind} price rows")
    if rows:
        old = rows[0]
        identical = all(
            _same(old[i], getattr(q, field))
            for i, field in enumerate(("open", "high", "low", "close"))
        )
        if kind == "DERIVATIVE":
            identical = identical and _same(old[4], q.volume)
        if not identical:
            return "PRESERVED_EXISTING_CONFLICT"
        if old[6] == candidate_id:
            return "UNCHANGED"
        # Identical legacy record: keep OHLCV unchanged, record provenance.
        _sql(
            connection,
            f'UPDATE "{table}" SET "PROVIDER_ID"={b}, '
            f'"CANDIDATE_ID"={b}, "SELECTION_REASON"={b}, '
            f'"SELECTED_AT"=CURRENT_TIMESTAMP WHERE {where}',
            (
                candidate.mapping.provider_id,
                candidate_id,
                "MATCHED_EXISTING",
                candidate.mapping.instrument,
                q.day.isoformat(),
            ),
        )
        return "MATCHED_EXISTING"
    fields = [key, "DATE", "OPEN", "HIGH", "LOW", "CLOSE"]
    params: list[object] = [
        candidate.mapping.instrument,
        q.day.isoformat(),
        *(_normalized(getattr(q, name)) for name in ("open", "high", "low", "close")),
    ]
    if kind == "DERIVATIVE":
        fields += ["VOLUME", "SETTLEMENT", "OPEN_INTEREST"]
        params += [
            q.volume,
            _normalized(q.settlement) if q.settlement is not None else None,
            q.open_interest,
        ]
    fields += ["SOURCE", "PROVIDER_ID", "CANDIDATE_ID", "SELECTION_REASON", "SELECTED_AT"]
    params += [
        f"multisource|{candidate.mapping.provider_code}",
        candidate.mapping.provider_id,
        candidate_id,
        reason,
    ]
    placeholders = [b] * (len(fields) - 1) + ["CURRENT_TIMESTAMP"]
    _sql(
        connection,
        f'INSERT INTO "{table}" '
        f"({','.join(chr(34) + x + chr(34) for x in fields)}) "
        f"VALUES ({','.join(placeholders)})",
        params,
    )
    return "INSERTED"


def run_market_job(
    connection: Any,
    *,
    kind: str,
    feeds: TypeMapping[str, MarketFeed],
    start: date,
    end: date,
    write: bool,
    backend: str = "snowflake",
    symbols: Sequence[str] | None = None,
) -> Result:
    if kind not in KINDS or backend not in ("snowflake", "duckdb") or start > end:
        raise ValueError("Invalid kind/backend/date range")
    mappings = load_mappings(connection, kind, symbols=symbols, backend=backend)
    added = promoted = identical = conflicts = rejected = empty = seen_days = selected_days = 0
    errors: list[str] = []
    for symbol, aliases in sorted(mappings.items()):
        daily: dict[date, list[Candidate]] = defaultdict(list)
        for alias in aliases:
            feed = feeds.get(alias.provider_code)
            if feed is None:
                errors.append(f"{symbol} {alias.provider_code}: adapter unavailable")
                continue
            low = max(start, alias.valid_from)
            high = min(end, alias.valid_to or end)
            if low > high:
                continue
            try:
                candles = feed.history(alias.provider_symbol, low, high)
                for candle in candles:
                    if not low <= candle.day <= high:
                        raise ValueError("Provider returned session outside mapped window")
                    daily[candle.day].append(make_candidate(kind, alias, candle))
            except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
                errors.append(f"{symbol} {alias.provider_code}: {type(exc).__name__}: {exc}")
                LOG.warning("SOURCE ERROR %s %s %s", symbol, alias.provider_code, exc)
        if not daily:
            empty += 1
        for day, candidates in sorted(daily.items()):
            seen_days += 1
            if len({c.mapping.provider_code for c in candidates}) != len(candidates):
                raise ValueError(f"Duplicate source/session for {symbol} {day}")
            selection, reason = choose(kind, candidates)
            selected_days += int(selection is not None)
            rejected += sum(c.validation_status != "VALID" for c in candidates)
            if not write:
                LOG.info(
                    "DRY RUN %s %s %s selected=%s",
                    symbol,
                    day,
                    reason,
                    selection.mapping.provider_code if selection else None,
                )
                continue
            with closing(connection.cursor()) as cur:
                try:
                    cur.execute("BEGIN")
                    ids = {}
                    for candidate in candidates:
                        candidate_id, created = save_candidate(connection, kind, candidate, backend)
                        ids[candidate.content_hash] = candidate_id
                        added += int(created)
                    if selection is not None:
                        outcome = promote(
                            connection,
                            kind,
                            selection,
                            ids[selection.content_hash],
                            reason,
                            backend,
                        )
                        if outcome == "PRESERVED_EXISTING_CONFLICT":
                            conflicts += 1
                        elif outcome in ("MATCHED_EXISTING", "UNCHANGED"):
                            identical += 1
                        else:
                            promoted += 1
                    cur.execute("COMMIT")
                    LOG.info(
                        "%s %s %s selected=%s result=%s",
                        symbol,
                        day,
                        reason,
                        selection.mapping.provider_code if selection else None,
                        outcome if selection is not None else "CANDIDATES_ONLY",
                    )
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
    return Result(
        len(mappings),
        added,
        promoted,
        identical,
        conflicts,
        rejected,
        tuple(errors),
        empty,
        seen_days,
        selected_days,
    )
