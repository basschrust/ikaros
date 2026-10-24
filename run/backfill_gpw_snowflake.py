"""Restartable historical GPW equity OHLCV backfill to Snowflake.

Dry-run by default. Use --write to persist only missing symbol/date rows.
The provider is OpenBB's existing yfinance equity implementation.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, timedelta
import logging
import math

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.gpw_backfill import missing_observations, normalize_prices, year_windows
from ingestion.prices import fetch_eod_prices

logger = logging.getLogger(__name__)


def registered_symbols(conn, symbols: list[str] | None, limit: int | None) -> list[str]:
    with closing(conn.cursor()) as cur:
        cur.execute('SELECT "SYMBOL" FROM "SECURITIES" WHERE "VENUE" = %s ORDER BY "SYMBOL"', ("GPW",))
        all_symbols = [str(row[0]) for row in cur.fetchall() if row[0]]
    if not all_symbols:
        raise RuntimeError("No GPW securities found: run seed_gpw_snowflake first")
    if symbols:
        requested = list(dict.fromkeys(symbols))
        unregistered = set(requested) - set(all_symbols)
        if unregistered:
            raise ValueError(f"Unregistered GPW symbols: {sorted(unregistered)}")
        all_symbols = requested
    return all_symbols[:limit] if limit is not None else all_symbols


def existing_dates(conn, symbol: str, start: date, end: date) -> set[date]:
    with closing(conn.cursor()) as cur:
        cur.execute(
            'SELECT DISTINCT TO_DATE("DATE") FROM "PRICES" '
            'WHERE "SYMBOL" = %s AND TO_DATE("DATE") BETWEEN %s AND %s',
            (symbol, start.isoformat(), end.isoformat()),
        )
        return {row[0] for row in cur.fetchall()}


def persist_batch(conn, frame: pd.DataFrame) -> int:
    """Stage typed rows and MERGE using explicit ISO dates; never overwrite existing rows.

    Temporary staging table is session-scoped, so it is safe to reuse on each chunk.
    Snowflake DDL commits automatically, hence stage creation is outside the DML transaction.
    """
    if frame.empty:
        return 0
    insert_sql = (
        'INSERT INTO "IKAROS_PRICE_STAGE" ("SYMBOL", "DATE", "OPEN", "HIGH", "LOW", '
        '"CLOSE", "VOLUME", "DIVIDEND", "SPLIT_RATIO") '
        "VALUES (%s, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD'), %s, %s, %s, %s, %s, %s, %s)"
    )
    merge_sql = '''MERGE INTO "PRICES" AS target
        USING "IKAROS_PRICE_STAGE" AS source
        ON target."SYMBOL" = source."SYMBOL" AND TO_DATE(target."DATE") = TO_DATE(source."DATE")
        WHEN NOT MATCHED THEN INSERT
        ("SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DIVIDEND", "SPLIT_RATIO")
        VALUES (source."SYMBOL", source."DATE", source."OPEN", source."HIGH", source."LOW",
                source."CLOSE", source."VOLUME", source."DIVIDEND", source."SPLIT_RATIO")'''
    rows = []
    for rec in frame.itertuples(index=False):
        values = []
        for field in ("open", "high", "low", "close", "volume", "dividend", "split_ratio"):
            value = getattr(rec, field)
            if pd.isna(value):
                values.append(None)
            else:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(f"Nonfinite {field} for {rec.symbol} {rec.date}")
                values.append(number)
        rows.append((rec.symbol, rec.date.isoformat(), *values))
    with closing(conn.cursor()) as cur:
        # Ensure the table exists for the session. DDL before transaction.
        cur.execute('CREATE TEMP TABLE IF NOT EXISTS "IKAROS_PRICE_STAGE" LIKE "PRICES"')
        try:
            cur.execute("BEGIN")
            cur.execute('DELETE FROM "IKAROS_PRICE_STAGE"')
            cur.executemany(insert_sql, rows)
            cur.execute(merge_sql)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", metavar="SYMBOL", help="Default: all seeded GPW symbols")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N registered securities")
    parser.add_argument("--write", action="store_true", help="Persist missing observations")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    windows = year_windows(args.start, args.end)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select database.driver: snowflake in config/database.yaml")

    failed = []
    attempted = 0
    missing_count = 0
    with repository_from_config(config) as repo:
        symbols = registered_symbols(repo.connection, args.symbols, args.limit)
        logger.info("GPW securities: %d, yearly periods: %d, write=%s", len(symbols), len(windows), args.write)
        for symbol in symbols:
            for begin, end in windows:
                try:
                    fetched = fetch_eod_prices(symbol=symbol, start_date=begin.isoformat(), end_date=(end + timedelta(days=1)).isoformat())
                    if fetched.empty:
                        logger.warning("NO_DATA %s %s..%s", symbol, begin, end)
                        continue
                    clean = normalize_prices(fetched, symbol, begin, end)
                    existing = existing_dates(repo.connection, symbol, begin, end)
                    missing = missing_observations(clean, existing)
                    attempted += len(clean)
                    missing_count += len(missing)
                    logger.info("%s %s..%s fetched=%d existing=%d missing=%d", symbol, begin, end,
                                len(clean), len(clean)-len(missing), len(missing))
                    if args.write and not missing.empty:
                        persist_batch(repo.connection, missing)
                        verified = existing_dates(repo.connection, symbol, begin, end)
                        gaps = set(missing["date"]) - verified
                        if gaps:
                            raise RuntimeError(f"Readback dates missing for {symbol}: {sorted(gaps)[:10]}")
                except Exception:
                    logger.exception("FAILED %s %s..%s", symbol, begin, end)
                    failed.append((symbol, begin, end))
                    if not args.continue_on_error:
                        raise
    logger.info("Finished: fetched=%d candidate_new=%d failures=%d", attempted, missing_count, len(failed))
    if failed:
        logger.error("Failed chunks: %s", ", ".join(f"{s}:{a}..{b}" for s, a, b in failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
