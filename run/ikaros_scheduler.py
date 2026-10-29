"""Ikaros collector scheduler: FX/GPW/index every 4h; derivatives every 6h.

Sinks remain separate. Jobs start immediately, and jobs of the same type never
run concurrently. --write is required for writes/publication (default dry-run).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
import subprocess
import sys
import time

LOG = logging.getLogger(__name__)
INTERVALS = {
    "FX": 4 * 360,
    "GPW": 4 * 3600,
    "INDEX": 4 * 3600,
    "DERIVATIVES": 6 * 3600,
    "FW20_PRICES": 14400,
}


def registered_symbols(dataset: str) -> list[str]:
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    if dataset == "GPW":
        sql = 'SELECT DISTINCT "SYMBOL" FROM "SECURITIES" WHERE "VENUE" = %s ORDER BY "SYMBOL"'
        params = ("GPW",)
    else:
        sql = 'SELECT DISTINCT "SYMBOL" FROM "CURRENCY_PAIRS" WHERE "IS_ACTIVE" = TRUE ORDER BY "SYMBOL"'
        params = ()
    with repository_from_config(load_config("database.yaml")) as repo:
        with repo.connection.cursor() as cur:
            cur.execute(sql, params)
            return [str(r[0]) for r in cur.fetchall() if r[0]]


def commands(job: str, *, fx_symbols: list[str], gpw_symbols: list[str], days: int, write: bool):
    if job == "FW20_PRICES":
        return [
            sys.executable,
            "-m",
            "run.refresh_fw20_prices",
        ] + (["--write"] if write else [])
    if job == "DERIVATIVES":
        return [sys.executable, "-m", "run.kafka_discover_contracts"] + (
            ["--write"] if write else []
        )
    if job == "INDEX":
        return [sys.executable, "-m", "run.refresh_index_prices", "--days", str(max(days, 10))] + (
            ["--write"] if write else []
        )
    symbols = fx_symbols if job == "FX" else gpw_symbols
    if not symbols:
        raise RuntimeError(f"No {job} symbols configured")
    return (
        [sys.executable, "-m", "run.kafka_collect", "--dataset", job, "--symbols"]
        + symbols
        + ["--days", str(days), "--continue-on-error"]
        + (["--write"] if write else [])
    )


def run_schedule(
    *,
    fx_symbols: list[str],
    days: int,
    write: bool,
    once: bool,
    gpw_limit: int | None,
    fx_all: bool,
):
    next_due = {job: 0.0 for job in INTERVALS}
    active: dict[str, subprocess.Popen] = {}
    attempts = {job: 0 for job in INTERVALS}
    failures = 0
    try:
        while True:
            now = time.monotonic()
            for job, proc in list(active.items()):
                code = proc.poll()
                if code is not None:
                    LOG.log(
                        logging.INFO if code == 0 else logging.ERROR, "%s ended exit=%d", job, code
                    )
                    failures += int(code != 0)
                    del active[job]
            for job, interval in INTERVALS.items():
                if job in active or now < next_due[job] or (once and attempts[job]):
                    continue
                try:
                    if job == "GPW":
                        all_symbols = registered_symbols("GPW")
                        symbols = all_symbols[:gpw_limit] if gpw_limit else all_symbols
                    else:
                        symbols = []
                    fx = registered_symbols("FX") if fx_all and job == "FX" else fx_symbols
                    cmd = commands(job, fx_symbols=fx, gpw_symbols=symbols, days=days, write=write)
                    env = os.environ.copy()
                    env.setdefault("IKAROS_KAFKA_BROKERS", "127.0.0.1:9092")
                    LOG.info(
                        "START %s symbols=%d at %s",
                        job,
                        len(fx) if job == "FX" else len(symbols) if job == "GPW" else 1,
                        datetime.now(timezone.utc).isoformat(),
                    )
                    active[job] = subprocess.Popen(cmd, env=env)
                except Exception:
                    LOG.exception("Unable to launch %s", job)
                    failures += 1
                next_due[job] = time.monotonic() + interval
                attempts[job] += 1
            if once and all(attempts.values()) and not active:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        LOG.info("Stopping scheduler; terminating active children")
        for proc in active.values():
            proc.terminate()
        for proc in active.values():
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    if once and failures:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fx-symbols", nargs="+", default=["EURPLN=X"])
    parser.add_argument(
        "--fx-all", action="store_true", help="May include unavailable derived FX symbols"
    )
    parser.add_argument("--gpw-limit", type=int, help="Limit GPW securities for pilot")
    parser.add_argument("--days", type=int, default=7, help="Collector rolling history window")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Publish FX/GPW/derivative events and persist index data",
    )
    parser.add_argument("--once", action="store_true", help="Run all jobs once and exit")
    args = parser.parse_args()
    if args.gpw_limit is not None and args.gpw_limit < 1:
        parser.error("--gpw-limit must be positive")
    if args.days < 1:
        parser.error("--days must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_schedule(
        fx_symbols=args.fx_symbols,
        days=args.days,
        write=args.write,
        once=args.once,
        gpw_limit=args.gpw_limit,
        fx_all=args.fx_all,
    )


if __name__ == "__main__":
    main()
