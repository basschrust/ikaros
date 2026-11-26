"""Print local Ikaros WIG20 readiness evidence without modifying market prices."""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any


def check(*, connection: Any, target: date) -> int:
    """Inspect official membership, PZU freshness, dated futures and real options."""

    def rows(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        return list(connection.execute(query, params).fetchall())

    membership = rows(
        'SELECT COUNT(*),COUNT(DISTINCT "SECURITY_SYMBOL"),MIN("VALID_FROM") '
        'FROM "INDEX_CONSTITUENTS" WHERE "INDEX_SYMBOL"=? '
        'AND "VALID_FROM"<=? AND ("VALID_TO" IS NULL OR "VALID_TO">=?)',
        ("WIG20", target, target),
    )[0]
    print(
        f"WIG20 official reference: rows={membership[0]}, symbols={membership[1]}, effective_from={membership[2]}"
    )
    last_pzu = rows(
        'SELECT MAX(CAST("DATE" AS DATE)) FROM "PRICES" '
        'WHERE "SYMBOL"=? AND CAST("DATE" AS DATE)<=?',
        ("PZU.WA", target),
    )[0][0]
    print(f"PZU.WA last close through {target}: {last_pzu}")
    futures = rows(
        'SELECT "CONTRACT_ID", "EXPIRY_DATE", "MULTIPLIER" FROM "DERIVATIVE_CONTRACTS" '
        'WHERE "INSTRUMENT_TYPE"=? AND "UNDERLYING_SYMBOL"=? AND "EXPIRY_DATE">? '
        'ORDER BY "EXPIRY_DATE"',
        ("FUTURE", "WIG20", target),
    )
    print(f"Live WIG20 futures: {len(futures)} (minimum requested: 3)")
    for cid, expiry, multiplier in futures:
        print(f"  {cid}: expiry={expiry}, multiplier={multiplier}")
    options = rows(
        'SELECT c."CONTRACT_ID",c."STRIKE",c."OPTION_SIDE",'
        'COUNT(DISTINCT CAST(p."DATE" AS DATE)) AS SESSIONS '
        'FROM "DERIVATIVE_CONTRACTS" c '
        'LEFT JOIN "DERIVATIVE_PRICES" p ON c."CONTRACT_ID"=p."CONTRACT_ID" '
        'AND CAST(p."DATE" AS DATE)<=? '
        'WHERE c."INSTRUMENT_TYPE"=? AND c."UNDERLYING_SYMBOL"=? '
        'AND c."EXPIRY_DATE">? GROUP BY c."CONTRACT_ID",c."STRIKE",c."OPTION_SIDE" '
        'ORDER BY SESSIONS DESC, c."CONTRACT_ID"',
        (target, "OPTION", "WIG20", target),
    )
    with_metadata = [x for x in options if x[1] is not None and x[2] in ("CALL", "PUT")]
    enough = [x for x in with_metadata if x[3] >= 4]
    print(
        f"Unexpired WIG20 options: {len(options)}, fully specified: {len(with_metadata)}, with >=4 EOD sessions: {len(enough)}"
    )
    for cid, strike, side, n in options[:12]:
        print(f"  {cid}: {side or '?'} strike={strike} historical_sessions={n}")
    failed = []
    if membership[0] != 20 or membership[1] != 20:
        failed.append("WIG20 MEMBERSHIP")
    if last_pzu != target:
        failed.append("PZU FRESHNESS")
    if len(futures) < 3 or any(x[2] is None for x in futures):
        failed.append("FUTURES REFERENCE")
    if not enough:
        failed.append("OPTION PRICE HISTORY")
    if failed:
        print("INCOMPLETE: " + ", ".join(failed))
        return 1
    print("BASIC COVERAGE OK: rerun Daedalus's stricter source validation")
    return 0


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", type=date.fromisoformat, default=date(2026, 11, 20))
    args = parser.parse_args(argv)
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "duckdb":
        parser.error("Requires IKAROS_DB_DRIVER=duckdb")
    with repository_from_config(config) as repo:
        return check(connection=repo.connection, target=args.through)


if __name__ == "__main__":
    raise SystemExit(main())
