"""Database-only reference resolution for candidate-first daily ingestion."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
from typing import Any, Sequence

from database.gpw_price_pipeline import Mapping

FOREIGN_VENUES = frozenset({"US", "PSE", "XETRA", "PARIS", "LSE", "BSE"})


def _query(conn: Any, sql: str, params: Sequence[object] = ()) -> list[tuple]:
    with closing(conn.cursor()) as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall()) if sql.lstrip().upper().startswith("SELECT") else []


def _day(val: object) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def active_foreign_mappings(
    conn: Any, *, venue: str, symbols: Sequence[str] | None = None
) -> list[Mapping]:
    if venue not in FOREIGN_VENUES:
        raise ValueError(f"Unsupported venue {venue}")
    sql = (
        """SELECT s."ASSET_ID",s."SYMBOL",p."PROVIDER_CODE",p."PROVIDER_ID",
        ps."PROVIDER_SYMBOL_ID",ps."PROVIDER_SYMBOL",p."PRIORITY",
        ps."VALID_FROM",ps."VALID_TO"
        FROM "SECURITIES" s JOIN "PROVIDER_SYMBOLS" ps ON ps."ASSET_ID"=s."ASSET_ID"
        JOIN "PROVIDERS" p ON p."PROVIDER_ID"=ps."PROVIDER_ID"
        WHERE s."VENUE"="""
        + "'"
        + venue
        + "'"
        + """ AND LOWER(s."INSTRUMENT_CLASS") IN ('stock','equity')
        AND COALESCE(s."IS_ACTIVE",TRUE)=TRUE AND COALESCE(ps."IS_ACTIVE",TRUE)=TRUE
        AND p."IS_ACTIVE"=TRUE AND p."PROVIDER_CODE"='YFINANCE' """
    )
    requested = set(symbols) if symbols is not None else None
    rows = []
    for asset, symbol, code, pid, psid, alias, priority, first, last in _query(conn, sql):
        if requested is not None and str(symbol) not in requested:
            continue
        rows.append(
            Mapping(
                int(asset),
                str(symbol),
                str(code),
                int(pid),
                int(psid),
                str(alias),
                int(priority),
                _day(first),
                _day(last) if last else None,
            )
        )
    found = {m.canonical_symbol for m in rows}
    if requested is not None and found != requested:
        raise ValueError(
            f"Missing YFINANCE mappings for {venue}: {sorted(requested - found)}; run setup"
        )
    if len({(m.asset_id, m.canonical_symbol) for m in rows}) != len(rows):
        raise ValueError("Multiple simultaneously active Yahoo mapping rows; resolve validity")
    return rows


def register_foreign_yahoo_mappings(conn: Any, *, backend: str, write: bool) -> int:
    """Seed ONLY Yahoo symbols already verified by legacy Ikaros equity backfills.

    Requires serialized execution. Existing mappings are never overwritten.
    """
    b = "%s" if backend == "snowflake" else "?"
    provider = _query(
        conn, 'SELECT "PROVIDER_ID" FROM "PROVIDERS" WHERE "PROVIDER_CODE"=\'YFINANCE\''
    )
    if len(provider) != 1:
        raise ValueError("Expected a unique YFINANCE provider; run existing provider seeder first")
    pid = int(provider[0][0])
    venues = ",".join("'" + v + "'" for v in sorted(FOREIGN_VENUES))
    assets = _query(
        conn,
        """SELECT "ASSET_ID","SYMBOL","VENUE" FROM "SECURITIES"
        WHERE "VENUE" IN ("""
        + venues
        + """)
        AND LOWER("INSTRUMENT_CLASS") IN ('stock','equity')
        AND COALESCE("IS_ACTIVE",TRUE)=TRUE""",
    )
    old = _query(
        conn,
        'SELECT "PROVIDER_SYMBOL_ID","ASSET_ID","PROVIDER_SYMBOL" FROM "PROVIDER_SYMBOLS" WHERE "PROVIDER_ID"='
        + b,
        (pid,),
    )
    existing: dict[int, list[tuple[int, str]]] = {}
    for identifier, asset, alias in old:
        existing.setdefault(int(asset), []).append((int(identifier), str(alias)))
    max_id = _query(conn, 'SELECT COALESCE(MAX("PROVIDER_SYMBOL_ID"),0) FROM "PROVIDER_SYMBOLS"')[
        0
    ][0]
    nxt = int(max_id)
    created = 0
    for asset, symbol, venue in sorted(assets, key=lambda r: int(r[0])):
        identifier = int(asset)
        alias = str(symbol)
        old_for_asset = existing.get(identifier, [])
        if old_for_asset:
            if {x[1] for x in old_for_asset} != {alias}:
                raise ValueError(f"Conflicting Yahoo aliases for {alias}: {old_for_asset}")
            continue
        created += 1
        if write:
            nxt += 1
            _query(
                conn,
                'INSERT INTO "PROVIDER_SYMBOLS" '
                '("PROVIDER_SYMBOL_ID","ASSET_ID","PROVIDER_ID","PROVIDER_SYMBOL","VALID_FROM") '
                f"VALUES ({b},{b},{b},{b},CAST({b} AS DATE))",
                (nxt, identifier, pid, alias, "1900-01-01"),
            )
    return created


def fx_pairs(conn: Any, *, symbols: Sequence[str] | None = None) -> dict[str, int]:
    rows = _query(
        conn, 'SELECT "SYMBOL","CURRENCY_PAIR_ID" FROM "CURRENCY_PAIRS" WHERE "IS_ACTIVE"=TRUE'
    )
    pairs = {}
    for symbol, identifier in rows:
        if str(symbol) in pairs:
            raise ValueError(f"Duplicate active currency pair {symbol}")
        pairs[str(symbol)] = int(identifier)
    if symbols is not None:
        wanted = set(symbols)
        unknown = wanted - set(pairs)
        if unknown:
            raise ValueError(f"Unregistered FX symbols: {sorted(unknown)}")
        return {s: pairs[s] for s in symbols}
    return pairs


def derivative_contracts(
    conn: Any, *, start: date, symbols: Sequence[str] | None = None
) -> list[str]:
    rows = _query(
        conn,
        'SELECT "CONTRACT_ID","EXPIRY_DATE","INSTRUMENT_TYPE" FROM "DERIVATIVE_CONTRACTS" '
        "WHERE \"INSTRUMENT_TYPE\" IN ('FUTURE','OPTION')",
    )
    wanted = set(symbols) if symbols is not None else None
    contracts = []
    for symbol, expiry, kind in rows:
        if _day(expiry) < start:
            continue
        if wanted is None or str(symbol) in wanted:
            contracts.append(str(symbol))
    if wanted is not None and set(contracts) != wanted:
        raise ValueError(f"Missing/expired registered contracts: {sorted(wanted - set(contracts))}")
    return sorted(contracts)
