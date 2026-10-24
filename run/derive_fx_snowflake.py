"""Derive FX closing prices from existing Snowflake FX_RATES observations.

Default is read-only dry run. With --write, stage and MERGE only missing
(date, currency_pair_id) records. Direct observations remain untouched.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.fx_cross_rates import CROSSES, derive_closes
from run.backfill_fx_snowflake import existing_pair_id

LOGGER = logging.getLogger(__name__)

STAGE_DDL = """
CREATE TEMPORARY TABLE IF NOT EXISTS IKAROS_FX_CROSS_STAGE (
    CURRENCY_PAIR_ID NUMBER(38,0), SYMBOL VARCHAR, TRADE_DATE VARCHAR,
    CLOSE FLOAT, COMMENT VARCHAR
)
"""
STAGE_INSERT = """
INSERT INTO IKAROS_FX_CROSS_STAGE
(CURRENCY_PAIR_ID, SYMBOL, TRADE_DATE, CLOSE, COMMENT)
VALUES (%s, %s, %s, %s, %s)
"""
MERGE_SQL = """
MERGE INTO FX_RATES AS dst
USING (
    SELECT CURRENCY_PAIR_ID, SYMBOL,
           TO_TIMESTAMP_NTZ(TRADE_DATE, 'YYYY-MM-DD') AS TRADE_TS,
           CLOSE, COMMENT
    FROM IKAROS_FX_CROSS_STAGE
) AS src
ON dst.CURRENCY_PAIR_ID = src.CURRENCY_PAIR_ID
   AND dst."DATE" = src.TRADE_TS
WHEN NOT MATCHED THEN INSERT (
    CURRENCY_PAIR_ID, SYMBOL, "DATE", OPEN, HIGH, LOW, CLOSE, VOLUME, COMMENT
) VALUES (
    src.CURRENCY_PAIR_ID, src.SYMBOL, src.TRADE_TS,
    NULL, NULL, NULL, src.CLOSE, NULL, src.COMMENT
)
"""


def fetch_close_rows(connection, symbol: str, start: date, end: date) -> pd.DataFrame:
    """Read directly from Snowflake; no Yahoo API calls required."""
    with connection.cursor() as cur:
        cur.execute(
            'SELECT TO_VARCHAR("DATE", \'YYYY-MM-DD\') AS TRADE_DATE, "CLOSE" '
            'FROM "FX_RATES" WHERE "SYMBOL" = %s '
            "AND \"DATE\" >= TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD') "
            "AND \"DATE\" < DATEADD(day, 1, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD')) "
            'ORDER BY "DATE"',
            (symbol, start.isoformat(), end.isoformat()),
        )
        records = cur.fetchall()
    return pd.DataFrame(records, columns=["date", "close"])


def target_dates(connection, pair_id: int, start: date, end: date) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT TO_VARCHAR("DATE", \'YYYY-MM-DD\') FROM "FX_RATES" '
            'WHERE "CURRENCY_PAIR_ID" = %s '
            "AND \"DATE\" >= TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD') "
            "AND \"DATE\" < DATEADD(day, 1, TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD'))",
            (pair_id, start.isoformat(), end.isoformat()),
        )
        return {str(item[0]) for item in cur.fetchall()}


def merge_closes(connection, pair_id: int, frame: pd.DataFrame) -> int:
    """Atomic insert-only MERGE, including a provenance comment."""
    if frame.empty:
        return 0
    batch = [
        (pair_id, row.symbol, row.date.isoformat(), float(row.close), row.comment)
        for row in frame.itertuples(index=False)
    ]
    with connection.cursor() as cur:
        cur.execute(STAGE_DDL)  # DDL outside transaction
        try:
            cur.execute("BEGIN")
            cur.execute("DELETE FROM IKAROS_FX_CROSS_STAGE")
            cur.executemany(STAGE_INSERT, batch)
            cur.execute(MERGE_SQL)
            inserted = int(cur.rowcount)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(CROSSES),
        help="Target cross pairs, e.g. CADPLN=X CNYPLN=X",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--write", action="store_true", help="Persist derived closes")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must be <= --end")
    unknown = set(args.symbols) - set(CROSSES)
    if unknown:
        parser.error(f"Unknown cross pair(s): {', '.join(sorted(unknown))}")
    settings = load_config("database.yaml")
    if settings.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Set database.driver: snowflake in config/database.yaml")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    failures = []
    total = 0
    with repository_from_config(settings) as repo:
        for symbol in dict.fromkeys(args.symbols):
            spec = CROSSES[symbol]
            try:
                pair_id = existing_pair_id(repo.connection, symbol)
                numerator = fetch_close_rows(repo.connection, spec.numerator, args.start, args.end)
                denominator = fetch_close_rows(
                    repo.connection, spec.denominator, args.start, args.end
                )
                if numerator.empty or denominator.empty:
                    LOGGER.warning(
                        "NO_SOURCE %s: %s=%d rows; %s=%d rows",
                        symbol,
                        spec.numerator,
                        len(numerator),
                        spec.denominator,
                        len(denominator),
                    )
                    continue
                derived = derive_closes(
                    numerator, denominator, spec, start=args.start, end=args.end
                )
                existing = target_dates(repo.connection, pair_id, args.start, args.end)
                new = derived.loc[~derived["date"].map(lambda d: d.isoformat()).isin(existing)]
                LOGGER.info(
                    "%s: aligned=%d existing=%d new=%d",
                    symbol,
                    len(derived),
                    len(existing),
                    len(new),
                )
                if args.write and not new.empty:
                    inserted = merge_closes(repo.connection, pair_id, new)
                    verified = target_dates(repo.connection, pair_id, args.start, args.end)
                    missing = {d.isoformat() for d in new["date"]} - verified
                    if missing:
                        raise RuntimeError(f"{symbol}: missing on readback: {sorted(missing)}")
                    total += inserted
                    LOGGER.info("%s: inserted=%d", symbol, inserted)
            except Exception:
                failures.append(symbol)
                LOGGER.exception("FAILED %s", symbol)
                if not args.continue_on_error:
                    raise
    LOGGER.info(
        "Finished: inserted=%d failed_pairs=%d dry_run=%s", total, len(failures), not args.write
    )
    if failures:
        raise SystemExit("Failed pairs: " + ", ".join(failures))


if __name__ == "__main__":
    main()
