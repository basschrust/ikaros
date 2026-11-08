"""Backfill actual GPW derivative contract prices from BiznesRadar, dry run by default.

Example: uv run --extra snowflake python -m run.backfill_derivative_prices \\
  --start 2026-07-01 --end 2026-09-30 --write
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import logging
from pathlib import Path
import time

from ikaros_app.derivative_prices import collect, persist, registered

LOG = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--symbols", nargs="+")
    ap.add_argument("--limit", type=int, help="Pilot: first N registered contracts")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument(
        "--html-dir", type=Path, help="Use offline SYMBOL.html and SYMBOL,2.html snapshots"
    )
    ap.add_argument("--report", type=Path, default=Path("output/derivative_prices_backfill.csv"))
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if args.start > args.end or args.limit is not None and args.limit < 1:
        ap.error("Invalid date window/limit")
    if args.timeout < 1 or args.max_pages < 1 or args.delay < 0:
        ap.error("Invalid timeout/max-pages/delay")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from ikaros_app.operations import load_environment
    import requests

    load_environment()
    failures = 0
    reports = []
    with repository_from_config(load_config("database.yaml")) as repo:
        contracts = registered(repo.connection, args.symbols)
        if args.limit:
            contracts = contracts[: args.limit]
        LOG.info(
            "BACKFILL contracts=%d from=%s through=%s write=%s",
            len(contracts),
            args.start,
            args.end,
            args.write,
        )
        with requests.Session() as session:
            session.headers.update(
                {"User-Agent": "Mozilla/5.0 (compatible; IkarosMarketResearch/1.0)"}
            )
            for i, (symbol, kind, expiry) in enumerate(contracts, 1):
                result = {
                    "contract_id": symbol,
                    "instrument_type": kind,
                    "start": args.start.isoformat(),
                    "end": args.end.isoformat(),
                    "rows": 0,
                    "pages": 0,
                    "status": "",
                    "detail": "",
                }
                try:
                    rows, coverage, pages = collect(
                        session,
                        symbol,
                        args.start,
                        args.end,
                        timeout=args.timeout,
                        delay=args.delay,
                        max_pages=args.max_pages,
                        html_dir=args.html_dir,
                    )
                    result["pages"] = pages
                    result["rows"] = len(rows)
                    result["status"] = (
                        "NO_DATA" if not rows else ("WRITTEN" if args.write else "READY")
                    )
                    result["detail"] = coverage
                    if coverage == "UNVERIFIED_SOURCE_END":
                        # Never commit incomplete history under a success label.
                        result["status"] = "INCOMPLETE"
                        result["detail"] = (
                            "Source ended after full page without verifiable navigation"
                        )
                        failures += 1
                    elif args.write and rows:
                        persist(repo.connection, rows)
                except Exception as exc:
                    failures += 1
                    result["status"] = "FAILED"
                    result["detail"] = str(exc)
                    LOG.exception("FAILED %s", symbol)
                reports.append(result)
                LOG.info(
                    "%d/%d %s %s rows=%s %s",
                    i,
                    len(contracts),
                    symbol,
                    result["status"],
                    result["rows"],
                    result["detail"],
                )
                if args.html_dir is None and args.delay and i < len(contracts):
                    time.sleep(args.delay)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(reports[0])
            if reports
            else [
                "contract_id",
                "instrument_type",
                "start",
                "end",
                "rows",
                "pages",
                "status",
                "detail",
            ],
        )
        writer.writeheader()
        writer.writerows(reports)
    LOG.info(
        "SUMMARY contracts=%d observations=%d failures=%d report=%s",
        len(reports),
        sum(int(r["rows"]) for r in reports if r["status"] != "FAILED"),
        failures,
        args.report,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
