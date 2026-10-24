"""Restartable multi-year FX backfill to Snowflake (no automatic reference-data seeding).

Run from Ikaros project root. By default this is a read-only dry run.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta
from typing import Iterator

import pandas as pd
from openbb_core.provider.utils.errors import EmptyDataError

from config.config import load_config
from database.repositories.factory import repository_from_config
from run.ingest_fx_rates import fetch_fx_history

LOGGER = logging.getLogger(__name__)

STAGE_DDL = """
CREATE TEMPORARY TABLE IF NOT EXISTS IKAROS_FX_BACKFILL_STAGE (
    CURRENCY_PAIR_ID NUMBER(38,0), SYMBOL VARCHAR, TRADE_DATE VARCHAR,
    OPEN FLOAT, HIGH FLOAT, LOW FLOAT, CLOSE FLOAT, VOLUME FLOAT
)
"""

MERGE_SQL = """
MERGE INTO FX_RATES AS target
USING (
    SELECT CURRENCY_PAIR_ID, SYMBOL,
           TO_TIMESTAMP_NTZ(TRADE_DATE, 'YYYY-MM-DD') AS TRADE_TIMESTAMP,
           OPEN, HIGH, LOW, CLOSE, VOLUME
    FROM IKAROS_FX_BACKFILL_STAGE
) AS src
ON target.CURRENCY_PAIR_ID = src.CURRENCY_PAIR_ID
   AND target."DATE" = src.TRADE_TIMESTAMP
WHEN NOT MATCHED THEN INSERT
    (CURRENCY_PAIR_ID, SYMBOL, "DATE", OPEN, HIGH, LOW, CLOSE, VOLUME)
VALUES
    (src.CURRENCY_PAIR_ID, src.SYMBOL, src.TRADE_TIMESTAMP,
     src.OPEN, src.HIGH, src.LOW, src.CLOSE, src.VOLUME)
"""

INSERT_STAGE = """
INSERT INTO IKAROS_FX_BACKFILL_STAGE
(CURRENCY_PAIR_ID, SYMBOL, TRADE_DATE, OPEN, HIGH, LOW, CLOSE, VOLUME)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

logger = logging.getLogger(__name__)


def year_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Yield consecutive inclusive calendar-year windows."""
    if start > end:
        raise ValueError("start must not be later than end")
    current = start
    while current <= end:
        chunk_end = min(end, date(current.year, 12, 31))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def normalize_batch(
    frame: pd.DataFrame, pair_id: int, symbol: str, start: date, end: date
) -> list[tuple]:
    """Validate observations, normalize dates, discard duplicated response dates."""
    if frame.empty:
        return []
    required = {"date", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{symbol}: missing columns {sorted(missing)}")
    batch = frame.copy()
    batch["date"] = pd.to_datetime(batch["date"], errors="raise").dt.date
    batch = batch.loc[batch["date"].between(start, end)]
    if "volume" not in batch:
        batch["volume"] = None
    batch = batch.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    if batch[list(required - {"date"})].isna().any().any():
        raise ValueError(f"{symbol}: NULL OHLC detected; aborting batch")
    rows = []
    for row in batch.itertuples(index=False):

        def number(value):
            return None if pd.isna(value) else float(value)

        rows.append(
            (
                pair_id,
                symbol,
                row.date.isoformat(),
                number(row.open),
                number(row.high),
                number(row.low),
                number(row.close),
                number(row.volume),
            )
        )
    return rows


def existing_pair_id(connection, symbol: str) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT "CURRENCY_PAIR_ID" FROM "CURRENCY_PAIRS" '
            'WHERE "SYMBOL" = %s AND "IS_ACTIVE" = TRUE',
            (symbol,),
        )
        rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"{symbol}: expected one active currency pair, found {len(rows)}. "
            "Seed CURRENCIES/CURRENCY_PAIRS first."
        )
    return int(rows[0][0])


def persisted_dates(connection, pair_id: int, begin: date, end: date) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT TO_CHAR("DATE", \'YYYY-MM-DD\') FROM "FX_RATES" '
            'WHERE "CURRENCY_PAIR_ID" = %s '
            "AND \"DATE\" >= TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD') "
            "AND \"DATE\" < DATEADD(day, 1, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD'))",
            (pair_id, begin.isoformat(), end.isoformat()),
        )
        return {str(row[0]) for row in cur.fetchall()}


def merge_batch(connection, rows: list[tuple]) -> int:
    """Stage one batch and MERGE atomically. Return inserted row count."""
    if not rows:
        return 0
    with connection.cursor() as cur:
        cur.execute(STAGE_DDL)  # DDL outside transaction
        try:
            cur.execute("BEGIN")
            cur.execute("DELETE FROM IKAROS_FX_BACKFILL_STAGE")
            cur.executemany(INSERT_STAGE, rows)
            cur.execute(MERGE_SQL)
            inserted = int(cur.rowcount)  # with this MERGE only inserts are possible
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return inserted


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--symbols",
        nargs="+",
        default=["EURPLN=X"],
        help="Symbols configured in Snowflake CURRENCY_PAIRS",
    )
    p.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    p.add_argument("--write", action="store_true", help="Perform MERGE (default: dry run)")
    p.add_argument("--delay", type=float, default=0.5, help="Seconds between API requests")
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue other symbol/year batches when one fails",
    )
    args = p.parse_args()
    if not args.symbols or args.delay < 0:
        p.error("Provide at least one symbol and nonnegative delay")
    windows = list(year_windows(args.start, args.end))
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("config/database.yaml must set database.driver: snowflake")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    total_inserted = 0
    failures: list[str] = []
    with repository_from_config(config) as repo:
        for symbol in dict.fromkeys(args.symbols):
            try:
                pair_id = existing_pair_id(repo.connection, symbol)
            except Exception:
                if not args.continue_on_error:
                    raise
                LOGGER.exception("Missing reference data for %s", symbol)
                failures.append(symbol)
                continue
            for begin, end in windows:
                label = f"{symbol} {begin}..{end}"
                try:
                    # Fetch first; compare existing valid dates before writing.
                    try:
                        df = fetch_fx_history(
                            symbol,
                            begin.isoformat(),
                            end.isoformat(),
                        )
                    except EmptyDataError:
                        logger.warning(
                            "NO_DATA %s %s..%s: provider returned no observations",
                            symbol,
                            begin,
                            end,
                        )
                        continue
                    except Exception:
                        logger.exception(
                            "FAILED %s %s..%s",
                            symbol,
                            begin,
                            end,
                        )
                        failed_batches.append((symbol, begin, end))
                        if not args.continue_on_error:
                            raise
                        continue
                    rows = normalize_batch(df, pair_id, symbol, begin, end)
                    existing = persisted_dates(repo.connection, pair_id, begin, end)
                    missing_rows = [row for row in rows if row[2] not in existing]
                    LOGGER.info(
                        "%s: fetched=%d existing=%d new=%d",
                        label,
                        len(rows),
                        len(existing),
                        len(missing_rows),
                    )
                    if args.write and missing_rows:
                        inserted = merge_batch(repo.connection, missing_rows)
                        confirmed = persisted_dates(repo.connection, pair_id, begin, end)
                        missing = {r[2] for r in missing_rows} - confirmed
                        if missing:
                            raise RuntimeError(f"Readback missing dates: {sorted(missing)}")
                        LOGGER.info(
                            "%s: inserted=%d verified=%d", label, inserted, len(missing_rows)
                        )
                        total_inserted += inserted
                except Exception:
                    LOGGER.exception("FAILED %s", label)
                    failures.append(label)
                    if not args.continue_on_error:
                        raise
                finally:
                    if args.delay:
                        time.sleep(args.delay)
    LOGGER.info(
        "Finished: inserted=%d failed_batches=%d dry_run=%s",
        total_inserted,
        len(failures),
        not args.write,
    )
    if failures:
        raise SystemExit("Failed batches: " + "; ".join(failures))


if __name__ == "__main__":
    main()
