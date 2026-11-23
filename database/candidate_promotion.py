"""Independent promotions from *stored* equity/index/derivative candidate rows.

No source-adapter calls or provider HTTP occur here. Newest correction from a
provider supersedes its older candidates; it never counts as a second source.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from database.candidate_observations import PromotionReport, promote_stored
from database.gpw_price_pipeline import (
    Candidate as EquityCandidate,
    Mapping as EquityMapping,
    choose as choose_equity,
    promote as promote_equity,
)
from database.market_quote_pipeline import (
    Candidate as MarketCandidate,
    ProviderMapping,
    choose as choose_market,
    promote as promote_market,
    validate as validate_market,
)
from ingestion.gpw_provider_feeds import RawCandle
from ingestion.market_quote_feeds import MarketCandle


def _rows(conn: Any, query: str, params: Sequence[object]) -> list[tuple]:
    with closing(conn.cursor()) as cur:
        cur.execute(query, tuple(params))
        return list(cur.fetchall())


def _date(val: object) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def _dec(val: object) -> Decimal | None:
    return Decimal(str(val)) if val is not None else None


def _execute_promotions(
    conn: Any, grouped: dict[tuple[str, date], list[tuple[int, Any]]], *, domain: str, backend: str
) -> PromotionReport:
    promoted = identical = conflicts = invalid = 0
    for (symbol, day), items in sorted(grouped.items()):
        # Query ordered by descending integer candidate_id: use most recent
        # revision for each independent provider.
        by_source = {}
        for candidate_id, candidate in items:
            by_source.setdefault(candidate.mapping.provider_code, (candidate_id, candidate))
        choices = list(by_source.values())
        if domain == "EQUITY":
            selection = choose_equity([c for _, c in choices])
            if selection.candidate is None:
                invalid += 1
                continue
            selected_id = next(identifier for identifier, c in choices if c is selection.candidate)
        else:
            kind = domain
            chosen, reason = choose_market(kind, [c for _, c in choices])
            if chosen is None:
                invalid += 1
                continue
            selected_id = next(identifier for identifier, c in choices if c is chosen)
        with closing(conn.cursor()) as cur:
            try:
                cur.execute("BEGIN")
                if domain == "EQUITY":
                    status = promote_equity(
                        conn, selection, canonical_symbol=symbol, candidate_id=selected_id
                    )
                else:
                    status = promote_market(conn, domain, chosen, selected_id, reason, backend)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        if status in ("INSERTED", "REPAIRED_SUSPECT"):
            promoted += 1
        elif status == "PRESERVED_EXISTING_CONFLICT":
            conflicts += 1
        else:
            identical += 1
    return PromotionReport(len(grouped), promoted, identical, conflicts, invalid)


def promote_equities(
    conn: Any, *, backend: str, start: date, end: date, symbols: Sequence[str] | None = None
) -> PromotionReport:
    """Promote GPW and foreign equities from shared PRICES_CANDIDATES."""
    b = "%s" if backend == "snowflake" else "?"
    sql = """SELECT c."CANDIDATE_ID",c."CONTENT_HASH",s."ASSET_ID",s."SYMBOL",
        p."PROVIDER_CODE",p."PROVIDER_ID",ps."PROVIDER_SYMBOL_ID",ps."PROVIDER_SYMBOL",
        p."PRIORITY",ps."VALID_FROM",ps."VALID_TO",c."DATE",
        c."OPEN",c."HIGH",c."LOW",c."CLOSE",c."VOLUME",
        c."VALIDATION_STATUS",c."VALIDATION_REASON"
        FROM "PRICES_CANDIDATES" c
        JOIN "SECURITIES" s ON s."ASSET_ID"=c."ASSET_ID"
        JOIN "PROVIDER_SYMBOLS" ps ON ps."PROVIDER_SYMBOL_ID"=c."PROVIDER_SYMBOL_ID"
        JOIN "PROVIDERS" p ON p."PROVIDER_ID"=c."PROVIDER_ID"
        WHERE CAST(c."DATE" AS DATE) BETWEEN CAST({b} AS DATE) AND CAST({b} AS DATE)
        AND p."IS_ACTIVE"=TRUE AND ps."IS_ACTIVE"=TRUE
        ORDER BY c."CANDIDATE_ID" DESC""".format(b=b)
    grouped: dict[tuple[str, date], list[tuple[int, EquityCandidate]]] = defaultdict(list)
    wanted = set(symbols) if symbols is not None else None
    for row in _rows(conn, sql, (start.isoformat(), end.isoformat())):
        (
            cid,
            hash_,
            asset_id,
            symbol,
            code,
            pid,
            psid,
            provider_symbol,
            priority,
            first,
            last,
            dt,
            op,
            hi,
            lo,
            cl,
            vol,
            status,
            reason,
        ) = row
        if wanted is not None and str(symbol) not in wanted:
            continue
        day = _date(dt)
        mapping = EquityMapping(
            int(asset_id),
            str(symbol),
            str(code),
            int(pid),
            int(psid),
            str(provider_symbol),
            int(priority),
            _date(first),
            _date(last) if last else None,
        )
        candle = RawCandle(
            day, _dec(op), _dec(hi), _dec(lo), _dec(cl), int(vol) if vol is not None else None
        )
        candidate = EquityCandidate(
            mapping, candle, str(hash_), str(status), str(reason) if reason else None
        )
        grouped[(str(symbol), day)].append((int(cid), candidate))
    return _execute_promotions(conn, grouped, domain="EQUITY", backend=backend)


def promote_market_prices(
    conn: Any,
    *,
    kind: str,
    backend: str,
    start: date,
    end: date,
    symbols: Sequence[str] | None = None,
) -> PromotionReport:
    if kind not in ("INDEX", "DERIVATIVE"):
        raise ValueError(kind)
    b = "%s" if backend == "snowflake" else "?"
    mapping = f"{kind}_PROVIDER_SYMBOLS"
    candidates = f"{kind}_PRICE_CANDIDATES"
    key = "INDEX_SYMBOL" if kind == "INDEX" else "CONTRACT_ID"
    sql = f'''SELECT c."CANDIDATE_ID",c."CONTENT_HASH",c."{key}",p."PROVIDER_ID",
        p."PROVIDER_CODE",m."PROVIDER_SYMBOL_ID",m."PROVIDER_SYMBOL",p."PRIORITY",
        m."VALID_FROM",m."VALID_TO",c."DATE",c."OPEN",c."HIGH",c."LOW",c."CLOSE",
        c."VOLUME",c."SETTLEMENT",c."OPEN_INTEREST",c."VALIDATION_STATUS",c."VALIDATION_REASON"
        FROM "{candidates}" c JOIN "{mapping}" m
        ON c."PROVIDER_SYMBOL_ID"=m."PROVIDER_SYMBOL_ID"
        JOIN "PROVIDERS" p ON p."PROVIDER_ID"=m."PROVIDER_ID"
        WHERE CAST(c."DATE" AS DATE) BETWEEN CAST({b} AS DATE) AND CAST({b} AS DATE)
        AND p."IS_ACTIVE"=TRUE AND m."IS_ACTIVE"=TRUE
        ORDER BY c."CANDIDATE_ID" DESC'''
    grouped: dict[tuple[str, date], list[tuple[int, MarketCandidate]]] = defaultdict(list)
    wanted = set(symbols) if symbols is not None else None
    for row in _rows(conn, sql, (start.isoformat(), end.isoformat())):
        (
            cid,
            hash_,
            symbol,
            pid,
            code,
            psid,
            alias,
            priority,
            first,
            last,
            dt,
            op,
            hi,
            lo,
            cl,
            vol,
            settlement,
            oi,
            status,
            reason,
        ) = row
        if wanted is not None and str(symbol) not in wanted:
            continue
        day = _date(dt)
        mp = ProviderMapping(
            str(symbol),
            int(pid),
            str(code),
            int(psid),
            str(alias),
            int(priority),
            _date(first),
            _date(last) if last else None,
        )
        candle = MarketCandle(
            day,
            _dec(op),
            _dec(hi),
            _dec(lo),
            _dec(cl),
            int(vol) if vol is not None else None,
            _dec(settlement),
            int(oi) if oi is not None else None,
        )
        validation = validate_market(kind, candle)[0]
        if status == "VALID" and validation != "VALID":
            raise ValueError(f"Stored {kind} candidate {cid} no longer passes validation")
        grouped[(str(symbol), day)].append(
            (
                int(cid),
                MarketCandidate(
                    mp, candle, str(hash_), str(status), str(reason) if reason else None
                ),
            )
        )
    return _execute_promotions(conn, grouped, domain=kind, backend=backend)


def promote_dataset(
    conn: Any,
    *,
    dataset: str,
    backend: str,
    start: date,
    end: date,
    symbols: Sequence[str] | None = None,
) -> PromotionReport:
    if dataset in ("GPW", "US", "PSE", "XETRA", "PARIS", "LSE", "BSE", "EQUITY"):
        return promote_equities(conn, backend=backend, start=start, end=end, symbols=symbols)
    if dataset in ("WIG20", "INDEX"):
        return promote_market_prices(
            conn, kind="INDEX", backend=backend, start=start, end=end, symbols=symbols
        )
    if dataset in ("DERIVATIVES", "FW20", "DERIVATIVE"):
        return promote_market_prices(
            conn, kind="DERIVATIVE", backend=backend, start=start, end=end, symbols=symbols
        )
    if dataset in ("FX", "RATE", "RATES"):
        return promote_stored(
            conn,
            kind="FX" if dataset == "FX" else "RATE",
            backend=backend,
            start=start,
            end=end,
            symbols=symbols,
        )
    raise ValueError(f"Unsupported dataset {dataset!r}")
