"""Snowflake/DuckDB candidate storage and promotion for FX and yield curves.

Every price change follows: fetch -> append candidate -> independent promotion.
All SQL, backend-sensitive conversions and transaction handling stay here.
No synthetic OHLC is created for NBP reference mid-rates or rate curves.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal, Sequence

Backend = Literal["snowflake", "duckdb"]
Kind = Literal["FX", "RATE"]


@dataclass(frozen=True)
class Observation:
    kind: Kind
    symbol: str
    day: date
    source: str
    ref_id: int | None = None
    quote_kind: str = "MARKET"  # MARKET or REFERENCE for FX; RATE for rate-series
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: int | None = None
    rate: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None


@dataclass(frozen=True)
class StoredCandidate:
    candidate_id: int
    observation: Observation
    status: str


@dataclass(frozen=True)
class PromotionReport:
    considered: int = 0
    promoted: int = 0
    identical: int = 0
    conflicts: int = 0
    invalid: int = 0


def _read_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid numeric source value: {value!r}") from exc
    return result


def _stored_decimal(value: Decimal | None, *, places: int = 8) -> str | None:
    if value is None or not value.is_finite():
        return None
    quantizer = Decimal(1).scaleb(-places)
    try:
        fixed = value.quantize(quantizer)
    except (ArithmeticError, InvalidOperation):
        return None
    if fixed != value or abs(fixed) >= Decimal("1e16"):
        return None
    return format(fixed, "f")


def _valid(observation: Observation) -> tuple[str, str | None]:
    o = observation
    if not o.symbol or not o.source or len(o.symbol) > 128 or len(o.source) > 128:
        return "REJECTED", "INVALID_IDENTITY"
    if o.kind == "FX":
        if o.ref_id is None or o.ref_id < 1 or o.quote_kind not in ("MARKET", "REFERENCE"):
            return "REJECTED", "INVALID_FX_REFERENCE"
        if o.close is None or not o.close.is_finite() or o.close <= 0:
            return "REJECTED", "INVALID_CLOSE"
        if o.quote_kind == "MARKET":
            vals = (o.open, o.high, o.low, o.close)
            if any(v is None or not v.is_finite() or v <= 0 for v in vals):
                return "REJECTED", "INVALID_OHLC"
            assert o.open is not None and o.high is not None and o.low is not None
            if o.low > min(o.open, o.close) or o.high < max(o.open, o.close) or o.low > o.high:
                return "REJECTED", "INCONSISTENT_OHLC"
        elif any(x is not None for x in (o.open, o.high, o.low, o.volume)):
            return "REJECTED", "REFERENCE_MUST_BE_CLOSE_ONLY"
        if o.volume is not None and (not isinstance(o.volume, int) or o.volume < 0):
            return "REJECTED", "INVALID_VOLUME"
        fields = (o.open, o.high, o.low, o.close)
        if any(v is not None and _stored_decimal(v) is None for v in fields):
            return "REJECTED", "UNREPRESENTABLE_OHLC"
        if o.volume is not None and o.volume >= 10**38:
            return "REJECTED", "UNREPRESENTABLE_VOLUME"
    elif o.kind == "RATE":
        if o.quote_kind != "RATE" or o.rate is None or not o.rate.is_finite():
            return "REJECTED", "INVALID_RATE"
        if any(
            v is not None and _stored_decimal(v, places=10) is None for v in (o.rate, o.bid, o.ask)
        ):
            return "REJECTED", "UNREPRESENTABLE_RATE"
        if o.bid is not None and o.ask is not None and o.bid > o.ask:
            return "REJECTED", "BID_EXCEEDS_ASK"
    else:
        return "REJECTED", "INVALID_KIND"
    return "VALID", None


def _fingerprint(o: Observation) -> str:
    fields = (
        o.kind,
        o.symbol,
        o.day.isoformat(),
        o.source,
        o.ref_id,
        o.quote_kind,
        o.open,
        o.high,
        o.low,
        o.close,
        o.volume,
        o.rate,
        o.bid,
        o.ask,
    )
    normalized = [
        format(v.normalize(), "f") if isinstance(v, Decimal) and v.is_finite() else str(v)
        for v in fields
    ]
    return hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()


def _bind(backend: Backend) -> str:
    return "%s" if backend == "snowflake" else "?"


def _execute(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, tuple(params))
        if sql.lstrip().upper().startswith("SELECT"):
            return list(cur.fetchall())
    return []


def _table(kind: Kind) -> str:
    return "FX_RATE_CANDIDATES" if kind == "FX" else "RATE_OBSERVATION_CANDIDATES"


def ensure_schema(connection: Any, *, backend: Backend) -> None:
    """Additive schema evolution. Call after the base Ikaros tables are ready."""
    if backend not in ("snowflake", "duckdb"):
        raise ValueError("Only Snowflake and DuckDB supported")
    bigint = "NUMBER(38,0)" if backend == "snowflake" else "BIGINT"
    money = "NUMBER(24,8)" if backend == "snowflake" else "DECIMAL(24,8)"
    rate = "NUMBER(24,10)" if backend == "snowflake" else "DECIMAL(24,10)"
    timestamp = "TIMESTAMP_NTZ" if backend == "snowflake" else "TIMESTAMP"
    _execute(
        connection,
        f"""CREATE TABLE IF NOT EXISTS "RATE_SERIES" (
        "SERIES_ID" VARCHAR(128) NOT NULL, "CURRENCY" VARCHAR,
        "SOURCE" VARCHAR, "CURVE_TYPE" VARCHAR, "TENOR" VARCHAR,
        "SOURCE_KEY" VARCHAR, "COMPOUNDING" VARCHAR, "DAY_COUNT" VARCHAR,
        "IS_ACTIVE" BOOLEAN DEFAULT TRUE, "CREATED_AT" {timestamp} DEFAULT CURRENT_TIMESTAMP)""",
    )
    _execute(
        connection,
        f"""CREATE TABLE IF NOT EXISTS "RATE_OBSERVATIONS" (
        "SERIES_ID" VARCHAR(128), "DATE" DATE, "RATE" {rate},
        "SOURCE" VARCHAR, "BID" {rate}, "ASK" {rate},
        "INGESTED_AT" {timestamp} DEFAULT CURRENT_TIMESTAMP)""",
    )
    for kind in ("FX", "RATE"):
        table = _table(kind)
        _execute(
            connection,
            f'CREATE SEQUENCE IF NOT EXISTS "{table}_ID_SEQ" START WITH 1 INCREMENT BY 1',
        )
        if kind == "FX":
            _execute(
                connection,
                f'''CREATE TABLE IF NOT EXISTS "{table}" (
                "CANDIDATE_ID" {bigint} PRIMARY KEY,
                "CONTENT_HASH" VARCHAR(64) NOT NULL UNIQUE,
                "CURRENCY_PAIR_ID" {bigint} NOT NULL,
                "SYMBOL" VARCHAR(128) NOT NULL,
                "DATE" DATE NOT NULL,
                "SOURCE_CODE" VARCHAR(128) NOT NULL,
                "QUOTE_KIND" VARCHAR(16) NOT NULL,
                "OPEN" {money}, "HIGH" {money}, "LOW" {money},
                "CLOSE" {money}, "VOLUME" {bigint},
                "VALIDATION_STATUS" VARCHAR(16) NOT NULL,
                "VALIDATION_REASON" VARCHAR(128),
                "INGESTED_AT" {timestamp} DEFAULT CURRENT_TIMESTAMP)''',
            )
        else:
            _execute(
                connection,
                f'''CREATE TABLE IF NOT EXISTS "{table}" (
                "CANDIDATE_ID" {bigint} PRIMARY KEY,
                "CONTENT_HASH" VARCHAR(64) NOT NULL UNIQUE,
                "SERIES_ID" VARCHAR(128) NOT NULL,
                "DATE" DATE NOT NULL,
                "SOURCE_CODE" VARCHAR(128) NOT NULL,
                "RATE" {rate}, "BID" {rate}, "ASK" {rate},
                "VALIDATION_STATUS" VARCHAR(16) NOT NULL,
                "VALIDATION_REASON" VARCHAR(128),
                "INGESTED_AT" {timestamp} DEFAULT CURRENT_TIMESTAMP)''',
            )
    for table, columns in {
        "FX_RATES": {
            "CANDIDATE_ID": bigint,
            "SOURCE_CODE": "VARCHAR(128)",
            "SELECTION_REASON": "VARCHAR(128)",
            "SELECTED_AT": timestamp,
        },
        "RATE_OBSERVATIONS": {
            "CANDIDATE_ID": bigint,
            "SELECTION_REASON": "VARCHAR(128)",
            "SELECTED_AT": timestamp,
            "BID": rate,
            "ASK": rate,
        },
    }.items():
        if backend == "snowflake":
            meta = (
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_CATALOG=CURRENT_DATABASE() "
                f"AND TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME='{table}'"
            )
        else:
            meta = (
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE LOWER(TABLE_NAME)='{table.lower()}' AND TABLE_SCHEMA=CURRENT_SCHEMA()"
            )
        present = {str(r[0]).upper() for r in _execute(connection, meta)}
        for name, declaration in columns.items():
            if name not in present:
                _execute(connection, f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}')


def stage(
    connection: Any, observations: Iterable[Observation], *, backend: Backend, write: bool
) -> tuple[int, int]:
    """Persist only evidence. No authoritative table DML is permitted here."""
    received = inserted = 0
    b = _bind(backend)
    for observation in observations:
        received += 1
        status, reason = _valid(observation)
        if not write:
            continue
        table = _table(observation.kind)
        content_hash = _fingerprint(observation)
        with closing(connection.cursor()) as cur:
            try:
                cur.execute("BEGIN")
                cur.execute(
                    f'SELECT "CANDIDATE_ID" FROM "{table}" WHERE "CONTENT_HASH"={b}',
                    (content_hash,),
                )
                ids = cur.fetchall()
                if len(ids) > 1:
                    raise RuntimeError(f"Duplicate candidate fingerprint {table}: {content_hash}")
                if not ids:
                    nextval = (
                        f'"{table}_ID_SEQ".NEXTVAL'
                        if backend == "snowflake"
                        else f"nextval('{table}_ID_SEQ')"
                    )
                    dt = f"CAST({b} AS DATE)"
                    if observation.kind == "FX":
                        fields = '("CANDIDATE_ID","CONTENT_HASH","CURRENCY_PAIR_ID","SYMBOL","DATE","SOURCE_CODE","QUOTE_KIND","OPEN","HIGH","LOW","CLOSE","VOLUME","VALIDATION_STATUS","VALIDATION_REASON")'
                        params = (
                            content_hash,
                            observation.ref_id,
                            observation.symbol,
                            observation.day.isoformat(),
                            observation.source,
                            observation.quote_kind,
                            *(
                                _stored_decimal(v)
                                for v in (
                                    observation.open,
                                    observation.high,
                                    observation.low,
                                    observation.close,
                                )
                            ),
                            observation.volume,
                            status,
                            reason,
                        )
                        binds = [nextval, b, b, b, dt, b, b, b, b, b, b, b, b, b]
                    else:
                        fields = '("CANDIDATE_ID","CONTENT_HASH","SERIES_ID","DATE","SOURCE_CODE","RATE","BID","ASK","VALIDATION_STATUS","VALIDATION_REASON")'
                        params = (
                            content_hash,
                            observation.symbol,
                            observation.day.isoformat(),
                            observation.source,
                            *(
                                _stored_decimal(v, places=10)
                                for v in (observation.rate, observation.bid, observation.ask)
                            ),
                            status,
                            reason,
                        )
                        binds = [nextval, b, b, dt, b, b, b, b, b, b]
                    cur.execute(
                        f'INSERT INTO "{table}" {fields} VALUES ({",".join(binds)})', params
                    )
                    inserted += 1
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
    return received, inserted


def register_rate_series(
    connection: Any, series: Iterable[Any], *, backend: Backend, write: bool
) -> None:
    """Reference-data registration only; does not write RATE_OBSERVATIONS."""
    b = _bind(backend)
    fields = ("currency", "source", "curve_type", "tenor", "source_key", "compounding", "day_count")
    for spec in series:
        existing = _execute(
            connection,
            f'SELECT "CURRENCY","SOURCE","CURVE_TYPE","TENOR","SOURCE_KEY","COMPOUNDING","DAY_COUNT" FROM "RATE_SERIES" WHERE "SERIES_ID"={b}',
            (spec.series_id,),
        )
        expected = tuple(getattr(spec, name) for name in fields)
        if len(existing) > 1 or (existing and tuple(existing[0]) != expected):
            raise ValueError(f"Ambiguous RATE_SERIES reference {spec.series_id}")
        if not existing and write:
            marks = ",".join(b for _ in range(8))
            _execute(
                connection,
                'INSERT INTO "RATE_SERIES" ("SERIES_ID","CURRENCY","SOURCE","CURVE_TYPE","TENOR","SOURCE_KEY","COMPOUNDING","DAY_COUNT") VALUES ('
                + marks
                + ")",
                (spec.series_id, *expected),
            )


def _read_candidates(
    connection: Any,
    *,
    kind: Kind,
    backend: Backend,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> list[StoredCandidate]:
    table = _table(kind)
    b = _bind(backend)
    cols = (
        '"CANDIDATE_ID","CONTENT_HASH","CURRENCY_PAIR_ID","SYMBOL","DATE","SOURCE_CODE","QUOTE_KIND",'
        '"OPEN","HIGH","LOW","CLOSE","VOLUME","VALIDATION_STATUS"'
        if kind == "FX"
        else '"CANDIDATE_ID","CONTENT_HASH","SERIES_ID","DATE","SOURCE_CODE","RATE","BID","ASK","VALIDATION_STATUS"'
    )
    query = (
        f'SELECT {cols} FROM "{table}" WHERE "DATE" BETWEEN CAST({b} AS DATE) '
        f'AND CAST({b} AS DATE) ORDER BY "CANDIDATE_ID" DESC'
    )
    rows = _execute(connection, query, (start.isoformat(), end.isoformat()))
    wanted = set(symbols) if symbols is not None else None
    result: list[StoredCandidate] = []
    for row in rows:
        if kind == "FX":
            (
                candidate_id,
                _h,
                ref_id,
                symbol,
                dt,
                source,
                quote_kind,
                op,
                hi,
                lo,
                cl,
                vol,
                status,
            ) = row
            o = Observation(
                kind="FX",
                symbol=str(symbol),
                day=_read_date(dt),
                source=str(source),
                ref_id=int(ref_id),
                quote_kind=str(quote_kind),
                open=_decimal(op),
                high=_decimal(hi),
                low=_decimal(lo),
                close=_decimal(cl),
                volume=int(vol) if vol is not None else None,
            )
        else:
            candidate_id, _h, symbol, dt, source, rate, bid, ask, status = row
            o = Observation(
                kind="RATE",
                symbol=str(symbol),
                day=_read_date(dt),
                source=str(source),
                quote_kind="RATE",
                rate=_decimal(rate),
                bid=_decimal(bid),
                ask=_decimal(ask),
            )
        if wanted is None or o.symbol in wanted:
            result.append(StoredCandidate(int(candidate_id), o, str(status)))
    return result


def _winner(rows: Sequence[StoredCandidate]) -> tuple[StoredCandidate | None, str]:
    # Observations are newest first. Use only latest revision from each provider,
    # never count two revisions from one source as independent corroboration.
    by_source: dict[str, StoredCandidate] = {}
    for row in rows:
        by_source.setdefault(row.observation.source, row)
    valid = [
        r for r in by_source.values() if r.status == "VALID" and _valid(r.observation)[0] == "VALID"
    ]
    if not valid:
        return None, "NO_VALID_SOURCE"

    # A complete FX market candle outranks NBP reference-only close; no mixing.
    def sort_key(r: StoredCandidate) -> tuple[int, int, str]:
        o = r.observation
        rank = 0 if o.kind == "FX" and o.quote_kind == "MARKET" else 1
        return rank, 0 if o.source.upper() == "YFINANCE" else 10, o.source

    winner = min(valid, key=sort_key)
    comparable = [r for r in valid if r.observation.quote_kind == winner.observation.quote_kind]

    def values(o: Observation) -> tuple[object, ...]:
        return (
            (o.open, o.high, o.low, o.close, o.volume) if o.kind == "FX" else (o.rate, o.bid, o.ask)
        )

    diverging = any(
        values(r.observation) != values(winner.observation) for r in comparable if r is not winner
    )
    reason = (
        "PRIORITY_SELECTED_DISAGREEMENT"
        if diverging
        else "MARKET_PREFERRED_REFERENCE_ALTERNATIVE"
        if winner.observation.kind == "FX"
        and len(comparable) != len(valid)
        and winner.observation.quote_kind == "MARKET"
        else "MULTI_SOURCE_AGREEMENT"
        if len(comparable) > 1
        else "SINGLE_VALID_SOURCE"
    )
    return winner, reason


def _same_value(a: Any, b: Any, *, tolerance: Decimal = Decimal(0)) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tolerance


def _promote_fx(connection: Any, row: StoredCandidate, reason: str, backend: Backend) -> str:
    o = row.observation
    b = _bind(backend)
    found = _execute(
        connection,
        f'SELECT "OPEN","HIGH","LOW","CLOSE","VOLUME","CANDIDATE_ID" FROM "FX_RATES" WHERE "CURRENCY_PAIR_ID"={b} AND CAST("DATE" AS DATE)=CAST({b} AS DATE)',
        (o.ref_id, o.day.isoformat()),
    )
    if len(found) > 1:
        raise ValueError(f"Duplicate authoritative FX {o.symbol} {o.day}")
    fields = (o.open, o.high, o.low, o.close, o.volume)
    if found:
        previous = found[0]
        if all(
            _same_value(previous[i], fields[i], tolerance=Decimal("0.00000001")) for i in range(5)
        ):
            if previous[5] == row.candidate_id:
                return "UNCHANGED"
            _execute(
                connection,
                f'UPDATE "FX_RATES" SET "CANDIDATE_ID"={b},"SOURCE_CODE"={b},"SELECTION_REASON"={b},"SELECTED_AT"=CURRENT_TIMESTAMP WHERE "CURRENCY_PAIR_ID"={b} AND CAST("DATE" AS DATE)=CAST({b} AS DATE)',
                (row.candidate_id, o.source, "MATCHED_EXISTING", o.ref_id, o.day.isoformat()),
            )
            return "MATCHED_EXISTING"
        # Reference mid-rate and market close are different quote types; preserve.
        return "PRESERVED_EXISTING_CONFLICT"
    cols = '("CURRENCY_PAIR_ID","SYMBOL","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME","CANDIDATE_ID","SOURCE_CODE","SELECTION_REASON","SELECTED_AT")'
    params = (
        o.ref_id,
        o.symbol,
        o.day.isoformat(),
        *(_stored_decimal(v) for v in fields[:4]),
        o.volume,
        row.candidate_id,
        o.source,
        reason,
    )
    binds = [b, b, f"CAST({b} AS TIMESTAMP)", b, b, b, b, b, b, b, b, "CURRENT_TIMESTAMP"]
    _execute(connection, f'INSERT INTO "FX_RATES" {cols} VALUES ({",".join(binds)})', params)
    return "INSERTED"


def _promote_rate(connection: Any, row: StoredCandidate, reason: str, backend: Backend) -> str:
    o = row.observation
    b = _bind(backend)
    found = _execute(
        connection,
        f'SELECT "RATE","BID","ASK","CANDIDATE_ID","SOURCE" FROM "RATE_OBSERVATIONS" WHERE "SERIES_ID"={b} AND CAST("DATE" AS DATE)=CAST({b} AS DATE)',
        (o.symbol, o.day.isoformat()),
    )
    if len(found) > 1:
        raise ValueError(f"Duplicate authoritative rate {o.symbol} {o.day}")
    fields = (o.rate, o.bid, o.ask)
    if found:
        previous = found[0]
        if all(
            _same_value(previous[i], fields[i], tolerance=Decimal("0.000000001")) for i in range(3)
        ) and (previous[4] is None or str(previous[4]) == o.source):
            if previous[3] == row.candidate_id:
                return "UNCHANGED"
            _execute(
                connection,
                f'UPDATE "RATE_OBSERVATIONS" SET "CANDIDATE_ID"={b},"SELECTION_REASON"={b},"SELECTED_AT"=CURRENT_TIMESTAMP WHERE "SERIES_ID"={b} AND CAST("DATE" AS DATE)=CAST({b} AS DATE)',
                (row.candidate_id, "MATCHED_EXISTING", o.symbol, o.day.isoformat()),
            )
            return "MATCHED_EXISTING"
        return "PRESERVED_EXISTING_CONFLICT"
    cols = '("SERIES_ID","DATE","RATE","SOURCE","BID","ASK","CANDIDATE_ID","SELECTION_REASON","SELECTED_AT")'
    vals = [b, f"CAST({b} AS DATE)", b, b, b, b, b, b, "CURRENT_TIMESTAMP"]
    params = (
        o.symbol,
        o.day.isoformat(),
        *(_stored_decimal(v, places=10) for v in (o.rate,)),
        o.source,
        _stored_decimal(o.bid, places=10),
        _stored_decimal(o.ask, places=10),
        row.candidate_id,
        reason,
    )
    _execute(
        connection, f'INSERT INTO "RATE_OBSERVATIONS" {cols} VALUES ({",".join(vals)})', params
    )
    return "INSERTED"


def promote_stored(
    connection: Any,
    *,
    kind: Kind,
    backend: Backend,
    start: date,
    end: date,
    symbols: Sequence[str] | None = None,
) -> PromotionReport:
    """Only reads committed candidates. Never invokes external providers."""
    stored = _read_candidates(
        connection, kind=kind, backend=backend, symbols=symbols, start=start, end=end
    )
    grouped: dict[tuple[str, date], list[StoredCandidate]] = {}
    for item in stored:
        grouped.setdefault((item.observation.symbol, item.observation.day), []).append(item)
    considered = promoted = identical = conflicts = invalid = 0
    for _, rows in sorted(grouped.items()):
        considered += 1
        selected, reason = _winner(rows)
        if selected is None:
            invalid += 1
            continue
        with closing(connection.cursor()) as cur:
            try:
                cur.execute("BEGIN")
                state = (
                    _promote_fx(connection, selected, reason, backend)
                    if kind == "FX"
                    else _promote_rate(connection, selected, reason, backend)
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        if state == "INSERTED":
            promoted += 1
        elif state == "PRESERVED_EXISTING_CONFLICT":
            conflicts += 1
        else:
            identical += 1
    return PromotionReport(considered, promoted, identical, conflicts, invalid)
