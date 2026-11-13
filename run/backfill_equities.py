"""Shared multi-venue daily equity backfill; registered symbols only, insert-only MERGE.

Persist OHLCV through fixed-point decimals, not Python binary floats.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, timedelta
import logging

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.decimal_prices import candle_params
from ingestion.gpw_backfill import missing_observations, normalize_prices, year_windows
from ingestion.prices import fetch_eod_prices
from ingestion.iic import BatchKey, SnowflakeIIC, Status

LOG = logging.getLogger(__name__)
LSE_PENCE_SYMBOLS = frozenset({"SHEL.L", "BP.L", "HSBA.L", "ULVR.L", "LSEG.L"})
VENUES = ("GPW", "US", "PSE", "XETRA", "PARIS", "LSE", "BSE")


def normalize_equity_quote_units(frame: pd.DataFrame, venue: str, symbol: str) -> pd.DataFrame:
    """Convert verified London GBp OHLC to GBP; never guess dividend currency."""
    if venue != "LSE" or frame.empty:
        return frame
    if symbol not in LSE_PENCE_SYMBOLS:
        raise ValueError(f"LSE quote units not verified for {symbol}")
    result = frame.copy()
    # Keep prior normalizer semantics; fixed-point conversion occurs at SQL binding.
    for field in ("open", "high", "low", "close"):
        result[field] = pd.to_numeric(result[field], errors="coerce") / 100
    if "dividend" in result:
        dividend = pd.to_numeric(result["dividend"], errors="coerce")
        uncertain = dividend.notna() & dividend.ne(0)
        if uncertain.any():
            LOG.warning(
                "LSE %s: %d dividend observation(s) set to NULL; "
                "Yahoo/OpenBB dividend denomination not verified",
                symbol,
                int(uncertain.sum()),
            )
        result["dividend"] = dividend.mask(uncertain)
    return result


def registered_symbols(
    conn, symbols: list[str] | None, limit: int | None, venue: str = "GPW"
) -> list[str]:
    if venue not in VENUES:
        raise ValueError(f"Unsupported equity venue {venue}")
    with closing(conn.cursor()) as cur:
        cur.execute(
            'SELECT "SYMBOL" FROM "SECURITIES" WHERE "VENUE" = %s ORDER BY "SYMBOL"',
            (venue,),
        )
        all_symbols = [str(row[0]) for row in cur.fetchall() if row[0]]
    if not all_symbols:
        raise RuntimeError(f"No {venue} securities registered; seed references first")
    if symbols:
        requested = list(dict.fromkeys(symbols))
        unregistered = set(requested) - set(all_symbols)
        if unregistered:
            raise ValueError(f"Unregistered {venue} symbols: {sorted(unregistered)}")
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
    """Insert missing candles using string->NUMBER casts, never float binds."""
    if frame.empty:
        return 0
    insert_sql = (
        'INSERT INTO "IKAROS_PRICE_STAGE" ("SYMBOL", "DATE", "OPEN", "HIGH", "LOW", '
        '"CLOSE", "VOLUME", "DIVIDEND", "SPLIT_RATIO") '
        "VALUES (%s, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD'), "
        "TO_DECIMAL(%s,24,8), TO_DECIMAL(%s,24,8), TO_DECIMAL(%s,24,8), "
        "TO_DECIMAL(%s,24,8), TO_DECIMAL(%s,38,0), TO_DECIMAL(%s,24,8), "
        "TO_DECIMAL(%s,24,8))"
    )
    merge_sql = """MERGE INTO "PRICES" AS target USING "IKAROS_PRICE_STAGE" AS source
        ON target."SYMBOL" = source."SYMBOL" AND TO_DATE(target."DATE") = TO_DATE(source."DATE")
        WHEN NOT MATCHED THEN INSERT
        ("SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DIVIDEND", "SPLIT_RATIO")
        VALUES (source."SYMBOL", source."DATE", source."OPEN", source."HIGH", source."LOW",
                source."CLOSE", source."VOLUME", source."DIVIDEND", source."SPLIT_RATIO")"""
    rows = [
        (rec.symbol, rec.date.isoformat(), *candle_params(rec))
        for rec in frame.itertuples(index=False)
    ]
    with closing(conn.cursor()) as cur:
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


def main(argv: list[str] | None = None, *, default_venue: str = "GPW") -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=VENUES, default=default_venue)
    parser.add_argument("--symbols", nargs="+", metavar="SYMBOL")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--iic-force", action="store_true")
    parser.add_argument("--iic-retry-no-data", action="store_true")
    parser.add_argument("--iic-lease-minutes", type=int, default=90)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    windows = year_windows(args.start, args.end)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select database.driver: snowflake in config/database.yaml")

    failed = []
    fetched_total = new_total = 0
    with repository_from_config(config) as repo:
        iic = SnowflakeIIC(repo.connection) if args.write else None
        if iic:
            iic.prepare()
        symbols = registered_symbols(repo.connection, args.symbols, args.limit, args.venue)
        LOG.info(
            "%s equity securities=%d yearly periods=%d write=%s",
            args.venue,
            len(symbols),
            len(windows),
            args.write,
        )
        for symbol in symbols:
            for begin, end in windows:
                key = BatchKey(args.venue, symbol, "yfinance", begin, end)
                if iic:
                    allowed, reason = iic.start(
                        key,
                        force=args.iic_force,
                        retry_no_data=args.iic_retry_no_data,
                        lease_minutes=args.iic_lease_minutes,
                    )
                    if not allowed:
                        LOG.info("SKIP %s %s..%s: %s", symbol, begin, end, reason)
                        continue
                fetched_count = candidate_count = 0
                try:
                    fetched = fetch_eod_prices(
                        symbol=symbol,
                        start_date=begin.isoformat(),
                        end_date=(end + timedelta(days=1)).isoformat(),
                    )
                    if fetched.empty:
                        LOG.warning("NO_DATA %s %s..%s", symbol, begin, end)
                        if iic:
                            iic.finish(key, Status.NO_DATA)
                        continue
                    fetched = normalize_equity_quote_units(fetched, args.venue, symbol)
                    clean = normalize_prices(fetched, symbol, begin, end, skip_invalid=True)
                    if clean.empty:
                        LOG.warning("NO_VALID_DATA %s %s..%s", symbol, begin, end)
                        if iic:
                            iic.finish(key, Status.NO_DATA)
                        continue
                    existing = existing_dates(repo.connection, symbol, begin, end)
                    missing = missing_observations(clean, existing)
                    fetched_count, candidate_count = len(clean), len(missing)
                    fetched_total += fetched_count
                    new_total += candidate_count
                    LOG.info(
                        "%s %s..%s fetched=%d existing=%d missing=%d",
                        symbol,
                        begin,
                        end,
                        fetched_count,
                        fetched_count - candidate_count,
                        candidate_count,
                    )
                    if args.write and not missing.empty:
                        persist_batch(repo.connection, missing)
                        verified = existing_dates(repo.connection, symbol, begin, end)
                        gaps = set(missing["date"]) - verified
                        if gaps:
                            raise RuntimeError(
                                f"Readback missing dates for {symbol}: {sorted(gaps)[:10]}"
                            )
                    if iic:
                        iic.finish(
                            key, Status.SUCCESS, fetched=fetched_count, candidate=candidate_count
                        )
                except Exception as exc:
                    if iic:
                        try:
                            iic.finish(
                                key,
                                Status.FAILED,
                                fetched=fetched_count,
                                candidate=candidate_count,
                                error=str(exc),
                            )
                        except Exception:
                            LOG.exception("Unable to record IIC failure for %s", symbol)
                    LOG.exception("FAILED %s %s..%s", symbol, begin, end)
                    failed.append((symbol, begin, end))
                    if not args.continue_on_error:
                        raise
    LOG.info(
        "%s finished: fetched=%d candidate_new=%d failures=%d",
        args.venue,
        fetched_total,
        new_total,
        len(failed),
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
