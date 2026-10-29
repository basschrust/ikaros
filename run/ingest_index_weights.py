"""Load a validated WIG20 weight snapshot into existing INDEX_CONSTITUENTS.

Historical snapshots are versioned as inclusive [VALID_FROM, VALID_TO] periods.
Dry-run by default. No table DDL is performed.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

TABLE = '"INDEX_CONSTITUENTS"'


def load_snapshot(path: Path) -> tuple[date, str, list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 20:
        raise ValueError(f"WIG20 snapshot must contain 20 rows; got {len(rows)}")
    dates = {date.fromisoformat(r["as_of"]) for r in rows}
    indices = {r["index_symbol"].strip().upper() for r in rows}
    if len(dates) != 1 or indices != {"WIG20"}:
        raise ValueError("Require exactly one as_of date and WIG20 index")
    tickers = [r["ticker"].strip().upper() for r in rows]
    if len(set(tickers)) != 20:
        raise ValueError("Duplicate ticker in WIG20 snapshot")
    if any(not r["source"].strip() or not r["source_url"].strip() for r in rows):
        raise ValueError("Missing source attribution")
    try:
        weights = [Decimal(r["weight_pct"]) for r in rows]
    except InvalidOperation as exc:
        raise ValueError("Invalid weight") from exc
    if any(not x.is_finite() or x <= 0 or x > 100 for x in weights):
        raise ValueError("Weights must be positive and finite")
    if abs(sum(weights) - Decimal("100")) > Decimal("0.20"):
        raise ValueError(f"Weight total {sum(weights)}% is inconsistent with 100%")
    parsed = [
        {"ticker": ticker, "weight_pct": weight, "source": row["source"].strip(),
         "source_url": row["source_url"].strip()}
        for ticker, weight, row in zip(tickers, weights, rows)
    ]
    return dates.pop(), "WIG20", parsed


def resolve_securities(conn, rows: list[dict]) -> list[dict]:
    """Match vendor short symbols to exactly one GPW security; store canonical SYMBOL."""
    with conn.cursor() as cur:
        cur.execute('SELECT "SYMBOL" FROM "SECURITIES" WHERE "VENUE" = %s', ("GPW",))
        securities = cur.fetchall()
    by_ticker: dict[str, list[str]] = {}
    for (symbol,) in securities:
        symbol = str(symbol)
        by_ticker.setdefault(symbol.upper().removesuffix(".WA"), []).append(symbol)
    unresolved = []
    for row in rows:
        matches = by_ticker.get(row["ticker"], [])
        if len(matches) != 1:
            unresolved.append((row["ticker"], len(matches)))
        else:
            row["security_symbol"] = matches[0]
    if unresolved:
        raise ValueError(f"Unknown or ambiguous GPW ticker(s): {unresolved}. Fix SECURITIES first.")
    return rows


def source_label(rows: list[dict]) -> str:
    """Existing schema has SOURCE only; retain attribution in this field."""
    sources = {(r["source"], r["source_url"]) for r in rows}
    if len(sources) != 1:
        raise ValueError("Snapshot uses multiple sources; cannot store a unique attribution")
    name, url = sources.pop()
    return f"{name} | {url}"


def persist(conn, as_of: date, index: str, rows: list[dict]) -> None:
    """Replace one snapshot and adjust adjacent validity intervals transactionally.

    Future snapshots remain untouched. Historical intervals use inclusive bounds;
    an open-ended VALID_TO=NULL means active until replaced.
    """
    if any("security_symbol" not in r for r in rows):
        raise ValueError("Resolve securities before writing")
    attribution = source_label(rows)
    with conn.cursor() as cur:
        try:
            cur.execute("BEGIN")
            # Find the subsequent version, if one already exists (backdated import).
            cur.execute(
                f'SELECT MIN("VALID_FROM") FROM {TABLE} '
                'WHERE "INDEX_SYMBOL"=%s AND "VALID_FROM">%s',
                (index, as_of),
            )
            (next_start,) = cur.fetchone()
            if next_start is not None and not isinstance(next_start, date):
                next_start = date.fromisoformat(str(next_start)[:10])
            end = next_start - timedelta(days=1) if next_start else None

            # Close all versions valid on the new snapshot date, except same-date
            # rows (which are replaced below). Protect manually curated future rows.
            cur.execute(
                f'UPDATE {TABLE} SET "VALID_TO"=DATEADD(day,-1,%s) '
                'WHERE "INDEX_SYMBOL"=%s AND "VALID_FROM"<%s '
                'AND ("VALID_TO" IS NULL OR "VALID_TO">=%s)',
                (as_of, index, as_of, as_of),
            )
            cur.execute(
                f'DELETE FROM {TABLE} WHERE "INDEX_SYMBOL"=%s AND "VALID_FROM"=%s',
                (index, as_of),
            )
            insert = (
                f'INSERT INTO {TABLE} ("INDEX_SYMBOL", "SECURITY_SYMBOL", "WEIGHT", '
                '"VALID_FROM", "VALID_TO", "SOURCE") VALUES (%s,%s,%s,%s,%s,%s)'
            )
            params = [
                (index, r["security_symbol"], float(r["weight_pct"] / Decimal(100)),
                 as_of, end, attribution)
                for r in rows
            ]
            cur.executemany(insert, params)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("data/wig20_weights_2026-10-22.csv"))
    parser.add_argument("--write", action="store_true", help="Persist in existing Snowflake table")
    args = parser.parse_args()
    as_of, index, rows = load_snapshot(args.csv)
    print(f'{index} {as_of}: {len(rows)} constituents; {sum(x["weight_pct"] for x in rows)}%')
    if not args.write:
        print("DRY RUN: CSV validated; Snowflake unchanged")
        return
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake driver in config/database.yaml")
    with repository_from_config(config) as repo:
        resolve_securities(repo.connection, rows)
        persist(repo.connection, as_of, index, rows)
        with repo.connection.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*), SUM("WEIGHT") FROM {TABLE} '
                'WHERE "INDEX_SYMBOL"=%s AND "VALID_FROM"=%s',
                (index, as_of),
            )
            count, total = cur.fetchone()
        if count != 20 or total is None or abs(Decimal(str(total)) - Decimal(1)) > Decimal("0.002"):
            raise RuntimeError(f"Unexpected post-write data: {count} rows, weight sum={total}")
        print(f"IMPORTED {count} rows into INDEX_CONSTITUENTS; fractional weight sum={total}")


if __name__ == "__main__":
    main()
