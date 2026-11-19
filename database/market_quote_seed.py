"""Seed reviewed index aliases and registered derivative contract symbols."""

from __future__ import annotations

from contextlib import closing
from datetime import date
from typing import Any

PROVIDERS = {
    "YFINANCE": ("Yahoo Finance", 10),
    "BIZNESRADAR": ("BiznesRadar", 30),
    "STOOQ": ("Stooq", 20),
}


def seed_mappings(connection: Any, *, backend: str, write: bool) -> dict[str, int]:
    """Seed the WIG20 aliases and exact registered derivative contract symbols.

    No Yahoo/Stooq derivatives are registered without verified ticker evidence.
    Call using a single writer (Snowflake primary keys are informational).
    """
    if backend not in ("snowflake", "duckdb"):
        raise ValueError(f"Invalid backend: {backend}")
    placeholder = "%s" if backend == "snowflake" else "?"
    with closing(connection.cursor()) as cur:
        cur.execute('SELECT "PROVIDER_ID", "PROVIDER_CODE" FROM "PROVIDERS"')
        providers = {str(code).upper(): int(pid) for pid, code in cur.fetchall()}
        absent = set(PROVIDERS) - set(providers)
        if absent:
            raise ValueError(f"Seed equity PROVIDERS first; missing {sorted(absent)}")
        cur.execute(
            'SELECT "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS" '
            "WHERE \"INSTRUMENT_TYPE\" IN ('FUTURE','OPTION')"
        )
        contracts = sorted({str(row[0]) for row in cur.fetchall()})
        planned = {
            "INDEX": [
                ("WIG20", "YFINANCE", "WIG20.WA"),
                ("WIG20", "BIZNESRADAR", "WIG20"),
                ("WIG20", "STOOQ", "wig20"),
            ],
            "DERIVATIVE": [(cid, "BIZNESRADAR", cid) for cid in contracts],
        }
        output: dict[str, int] = {}
        if write:
            cur.execute("BEGIN")
        try:
            for kind, rows in planned.items():
                table = f"{kind}_PROVIDER_SYMBOLS"
                key = "INDEX_SYMBOL" if kind == "INDEX" else "CONTRACT_ID"
                cur.execute(
                    f'SELECT "{key}", "PROVIDER_ID", "PROVIDER_SYMBOL", '
                    f'"VALID_FROM", "VALID_TO", "IS_ACTIVE" FROM "{table}"'
                )
                existing = {
                    (str(k), int(pid)): str(symbol)
                    for k, pid, symbol, _start, _end, _active in cur.fetchall()
                }
                additions = []
                for canonical, provider, symbol in rows:
                    key_pair = (canonical, providers[provider])
                    if key_pair in existing:
                        if existing[key_pair] != symbol:
                            raise ValueError(
                                f"Conflicting {kind} alias {canonical}/{provider}: "
                                f"{existing[key_pair]} != {symbol}"
                            )
                        continue
                    additions.append((canonical, providers[provider], symbol))
                output[kind] = len(additions)
                if not write:
                    continue
                sequence = f"{table}_ID_SEQ"
                expr = (
                    f'"{sequence}".NEXTVAL' if backend == "snowflake" else f"nextval('{sequence}')"
                )
                for canonical, pid, symbol in additions:
                    cur.execute(
                        f'INSERT INTO "{table}" '
                        f'("PROVIDER_SYMBOL_ID", "{key}", "PROVIDER_ID", '
                        f'"PROVIDER_SYMBOL", "VALID_FROM") '
                        f"VALUES ({expr},{placeholder},{placeholder},{placeholder},"
                        f"{placeholder})",
                        (canonical, pid, symbol, date(1990, 1, 1).isoformat()),
                    )
            if write:
                cur.execute("COMMIT")
        except Exception:
            if write:
                cur.execute("ROLLBACK")
            raise
        return output
