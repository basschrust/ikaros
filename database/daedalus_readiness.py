"""Read-only evidence for the starter WIG20 scenario, not a simulator guarantee."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


def price_dates(*, connection: Any, dataset: str, symbol: str, target: date) -> set[date]:
    # Whitelist identifiers; all values are bound parameters.
    table, key = {
        "GPW": ("PRICES", "SYMBOL"),
        "WIG20": ("INDEX_PRICES", "INDEX_SYMBOL"),
        "FX": ("FX_RATES", "SYMBOL"),
        "DERIVATIVES": ("DERIVATIVE_PRICES", "CONTRACT_ID"),
    }[dataset]
    rows = connection.execute(
        f'SELECT DISTINCT CAST("DATE" AS DATE) FROM "{table}" '
        f'WHERE "{key}"=? AND CAST("DATE" AS DATE)<=? AND "CLOSE">0',
        (symbol, target),
    ).fetchall()
    return {row[0] for row in rows}


def live_contracts(*, connection: Any, target: date) -> list[tuple[Any, ...]]:
    return connection.execute(
        'SELECT "CONTRACT_ID", "INSTRUMENT_TYPE", "EXPIRY_DATE", "MULTIPLIER", '
        '"STRIKE", "OPTION_SIDE" FROM "DERIVATIVE_CONTRACTS" '
        'WHERE "UNDERLYING_SYMBOL"=? AND "EXPIRY_DATE">? '
        'ORDER BY "EXPIRY_DATE", "CONTRACT_ID"',
        ("WIG20", target),
    ).fetchall()


def check(*, connection: Any, target: date, recent_sessions: tuple[date, ...],
          lookback: int = 252, futures_count: int = 3, options_count: int = 20) -> list[str]:
    problems = []
    members = connection.execute(
        'SELECT "SECURITY_SYMBOL" FROM "INDEX_CONSTITUENTS" WHERE "INDEX_SYMBOL"=? '
        'AND "VALID_FROM"<=? AND ("VALID_TO" IS NULL OR "VALID_TO">=?)',
        ("WIG20", target, target),
    ).fetchall()
    symbols = tuple(str(row[0]) for row in members)
    if len(symbols) != 20 or len(set(symbols)) != 20:
        problems.append(f"WIG20 membership: {len(set(symbols))}/20 unique symbols")
    index_dates = price_dates(connection=connection, dataset="WIG20", symbol="WIG20", target=target)
    required = set(sorted(index_dates)[-lookback:]) | set(recent_sessions)
    if len(index_dates) < lookback:
        problems.append(f"WIG20: {len(index_dates)}/{lookback} historical sessions")
    for dataset, symbol in [("WIG20", "WIG20"), *(("GPW", s) for s in symbols)]:
        dates = index_dates if dataset == "WIG20" else price_dates(
            connection=connection, dataset=dataset, symbol=symbol, target=target
        )
        missing = sorted(required - dates)
        if len(dates) < lookback or missing:
            problems.append(f"{symbol}: {len(dates)} closes, {len(missing)} missing required sessions"
                            + (f" (latest missing {missing[-1]})" if missing else ""))
    for symbol in ("EURPLN=X", "USDPLN=X"):
        dates = price_dates(connection=connection, dataset="FX", symbol=symbol, target=target)
        if not dates or max(dates) < recent_sessions[-min(3, len(recent_sessions))]:
            problems.append(f"FX {symbol}: missing recent quote")
    contracts = live_contracts(connection=connection, target=target)
    futures = [r for r in contracts if r[1] == "FUTURE"]
    # Match the nearest expiries (Daedalus selects its first requested contracts).
    if len(futures) < futures_count:
        problems.append(f"Futures registry: {len(futures)}/{futures_count} live contracts")
    for row in futures[:futures_count]:
        if row[3] != 20:
            problems.append(f"Future {row[0]}: missing or incorrect multiplier")
        dates = price_dates(connection=connection, dataset="DERIVATIVES", symbol=row[0], target=target)
        if target not in dates:
            problems.append(f"Future {row[0]}: no close on {target}")
    options = [r for r in contracts if r[1] == "OPTION" and r[3] == 10
               and r[4] is not None and r[5] in ("CALL", "PUT")]
    available = [r for r in options if len(price_dates(connection=connection, dataset="DERIVATIVES",
                 symbol=r[0], target=target)) >= 4]
    if not available:
        problems.append("Options: no live contract with four real historical closes")
    print(f"Options: {len(available)} with >=4 closes / {len(options)} registered; "
          f"Daedalus requests up to {options_count}")
    if len(available) < options_count:
        print("WARNING: fewer options with price history than requested; Daedalus may select fewer.")
    return problems


def check_file(*, path: Path, target: date, recent_sessions: tuple[date, ...]) -> list[str]:
    import duckdb

    with duckdb.connect(str(path), read_only=True) as connection:
        return check(connection=connection, target=target, recent_sessions=recent_sessions)
