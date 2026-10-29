"""Read recent FW20-series historical OHLCV and upsert verified source rows.

Source: https://www.biznesradar.pl/notowania-historyczne/<CONTRACT_ID>
Dry-run by default; no contract registration or price synthesis.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import logging
import time

from ingestion.fw20_history import CONTRACT, parse_history

LOG = logging.getLogger(__name__)
URL = "https://www.biznesradar.pl/notowania-historyczne/{}"


def registered(connection) -> list[str]:
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT DISTINCT "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS" '
            'WHERE "INSTRUMENT_TYPE" = %s AND "UNDERLYING_SYMBOL" = %s '
            'ORDER BY "CONTRACT_ID"',
            ("FUTURE", "WIG20"),
        )
        return [str(row[0]) for row in cur.fetchall() if CONTRACT.fullmatch(str(row[0]))]


def retrieve(symbol: str, *, html_dir: Path | None, timeout: int) -> str:
    if html_dir is not None:
        return (html_dir / f"{symbol}.html").read_text(encoding="utf-8")
    import requests

    response = requests.get(
        URL.format(symbol),
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; IkarosMarketResearch/1.0)"},
    )
    response.raise_for_status()
    return response.text


def persist(connection, rows: list[dict]) -> None:
    # Only overwrite rows already sourced from this same HTML series; do not
    # replace manual or independently sourced futures data.
    sql = """MERGE INTO "DERIVATIVE_PRICES" t USING (
        SELECT %s::VARCHAR AS cid, TO_DATE(%s) AS day,
               %s::FLOAT AS op, %s::FLOAT AS hi, %s::FLOAT AS lo,
               %s::FLOAT AS cl, %s::FLOAT AS vol, %s::VARCHAR AS src
    ) s ON t."CONTRACT_ID" = s.cid AND t."DATE" = s.day
    WHEN MATCHED AND t."SOURCE" = s.src THEN UPDATE SET
        t."OPEN"=s.op, t."HIGH"=s.hi, t."LOW"=s.lo,
        t."CLOSE"=s.cl, t."VOLUME"=s.vol,
        t."INGESTED_AT"=CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        ("CONTRACT_ID","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME",
         "OPEN_INTEREST","SETTLEMENT","SOURCE")
        VALUES (s.cid,s.day,s.op,s.hi,s.lo,s.cl,s.vol,NULL,NULL,s.src)"""
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            for r in rows:
                cur.execute(
                    sql,
                    (
                        r["contract_id"],
                        r["date"].isoformat(),
                        r["open"],
                        r["high"],
                        r["low"],
                        r["close"],
                        r["volume"],
                        r["source"],
                    ),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", help="Default: all registered FW20 contracts")
    p.add_argument("--days", type=int, default=12)
    p.add_argument("--through", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    p.add_argument("--html-dir", type=Path, help="Read saved <SYMBOL>.html files offline")
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.days < 1:
        p.error("--days must be positive")
    if args.timeout < 1:
        p.error("--timeout must be positive")
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    with repository_from_config(load_config("database.yaml")) as repo:
        symbols = args.symbols or registered(repo.connection)
        if not symbols:
            raise RuntimeError("No registered FW20 contracts; register contract candidates first")
        if any(not CONTRACT.fullmatch(s) for s in symbols):
            p.error("Only genuine FW20 quarterly contract IDs allowed")
        failures = []
        total = 0
        for i, symbol in enumerate(symbols):
            try:
                raw = retrieve(symbol, html_dir=args.html_dir, timeout=args.timeout)
                parsed = parse_history(raw, symbol=symbol, through=args.through)
                recent = [
                    r for r in parsed if r["date"] >= args.through - timedelta(days=args.days)
                ]
                if not recent:
                    raise RuntimeError("No completed historical candles in requested window")
                LOG.info(
                    "%s rows=%d first=%s last=%s write=%s",
                    symbol,
                    len(recent),
                    recent[0]["date"],
                    recent[-1]["date"],
                    args.write,
                )
                if args.write:
                    persist(repo.connection, recent)
                total += len(recent)
            except Exception:
                LOG.exception("FAILED %s", symbol)
                failures.append(symbol)
            if args.html_dir is None and i + 1 < len(symbols):
                time.sleep(1)
        LOG.info("FW20 contracts=%d rows=%d failures=%d", len(symbols), total, len(failures))
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
