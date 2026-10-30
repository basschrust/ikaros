"""Backfill registered FX pairs from official NBP Table A mid/reference rates.

Read-only dry run by default. Inserts only missing (currency_pair_id, date)
into FX_RATES. Source rows contain CLOSE only; OHLC/VOLUME stay NULL.

Example:
    uv run --extra snowflake python -m run.backfill_fx_nbp --start 2002-01-02 --end 2019-12-31 --write
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date
import logging
import time

from ingestion.nbp_fx import cross_rows, parse_table, windows

LOG = logging.getLogger("ikaros.fx.nbp")
API = "https://api.nbp.pl/api/exchangerates/tables/a/{}/{}/"


def registered_pairs(connection) -> dict[str, int]:
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT "SYMBOL", "CURRENCY_PAIR_ID" FROM "CURRENCY_PAIRS" WHERE "IS_ACTIVE" = TRUE'
        )
        rows = cur.fetchall()
    pairs = {}
    for symbol, pair_id in rows:
        if symbol in pairs:
            raise ValueError(f"Duplicate active FX reference symbol {symbol}")
        pairs[str(symbol)] = int(pair_id)
    return pairs


def fetch_tables(session, start: date, end: date, *, timeout: int = 30):
    url = API.format(start.isoformat(), end.isoformat())
    response = session.get(
        url,
        params={"format": "json"},
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "Ikaros/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected NBP response for {start}..{end}")
    return parse_table(payload)


def persist(connection, rows, ids: dict[str, int]):
    """Atomic insert-only MERGE per date-window, preserving all market-source rows."""
    if not rows:
        return 0
    stage = """CREATE TEMP TABLE IF NOT EXISTS "IKAROS_NBP_FX_STAGE" (
      "PID" NUMBER(38,0), "SYMBOL" VARCHAR, "TRADE_DATE" DATE,
      "CLOSE" FLOAT, "COMMENT" VARCHAR)"""
    insert = """INSERT INTO "IKAROS_NBP_FX_STAGE"
      ("PID","SYMBOL","TRADE_DATE","CLOSE","COMMENT")
      VALUES (%s,%s,TO_DATE(%s),%s,%s)"""
    merge = """MERGE INTO "FX_RATES" t USING "IKAROS_NBP_FX_STAGE" s
      ON t."CURRENCY_PAIR_ID"=s."PID" AND TO_DATE(t."DATE")=s."TRADE_DATE"
      WHEN NOT MATCHED THEN INSERT
      ("CURRENCY_PAIR_ID","SYMBOL","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME","COMMENT")
      VALUES (s."PID",s."SYMBOL",TO_TIMESTAMP_NTZ(s."TRADE_DATE"),
              NULL,NULL,NULL,s."CLOSE",NULL,s."COMMENT")"""
    batch = [(ids[s], s, d.isoformat(), c, comment) for s, d, c, comment in rows]
    with closing(connection.cursor()) as cur:
        cur.execute(stage)
        try:
            cur.execute("BEGIN")
            cur.execute('DELETE FROM "IKAROS_NBP_FX_STAGE"')
            cur.executemany(insert, batch)
            cur.execute(merge)
            inserted = int(cur.rowcount)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return inserted


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, default=date(2002, 1, 2))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument(
        "--symbols", nargs="+", help="Specific registered pairs; default all active pairs"
    )
    p.add_argument("--write", action="store_true", help="Insert missing reference closes")
    p.add_argument("--delay", type=float, default=0.25)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--continue-on-error", action="store_true")
    args = p.parse_args()
    if args.start < date(2002, 1, 2) or args.end < args.start:
        p.error("Choose a range from 2002-01-02, with end >= start")
    if args.delay < 0 or args.timeout < 1:
        p.error("Invalid delay or timeout")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import requests
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Select Snowflake in config/database.yaml")
    with repository_from_config(config) as repo, requests.Session() as session:
        pairs = registered_pairs(repo.connection)
        symbols = args.symbols or list(pairs)
        unknown = set(symbols) - set(pairs)
        if unknown:
            p.error(f"Unregistered active FX symbols: {sorted(unknown)}")
        total = 0
        errors = []
        for begin, end in windows(args.start, args.end):
            try:
                daily = fetch_tables(session, begin, end, timeout=args.timeout)
                rows = cross_rows(daily, symbols)
                added = persist(repo.connection, rows, pairs) if args.write else 0
                total += added
                LOG.info(
                    "%s..%s NBP_days=%d valid_pair_rows=%d inserted=%d",
                    begin,
                    end,
                    len(daily),
                    len(rows),
                    added,
                )
            except Exception as exc:
                LOG.exception("FAILED window %s..%s: %s", begin, end, exc)
                errors.append(f"{begin}..{end}")
                if not args.continue_on_error:
                    raise
            if args.delay:
                time.sleep(args.delay)
        LOG.info("FINISHED inserted=%d failures=%d dry_run=%s", total, len(errors), not args.write)
        if errors:
            raise SystemExit(f"Failed windows: {', '.join(errors)}")


if __name__ == "__main__":
    main()
