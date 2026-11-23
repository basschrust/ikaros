"""Read recent FW20-series historical OHLCV and upsert verified source rows.

Source: https://www.biznesradar.pl/notowania-historyczne/<CONTRACT_ID>
Dry-run by default; no contract registration or price synthesis.
"""

from __future__ import annotations

import argparse

from datetime import date, timedelta
from pathlib import Path
import logging
import time

from snowflake.connector import SnowflakeConnection

from ingestion.fw20_history import CONTRACT, parse_history

LOG = logging.getLogger(__name__)
URL = "https://www.biznesradar.pl/notowania-historyczne/{}"


def registered(connection: SnowflakeConnection) -> list[str]:
    from database.isolated.run_refresh_fw20_prices import registered as _isolated_impl

    return _isolated_impl(connection)


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


def persist(connection: SnowflakeConnection, rows: list[dict]) -> None:
    from database.isolated.run_refresh_fw20_prices import persist as _isolated_impl

    return _isolated_impl(connection, rows)


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
