"""Index-price persistence and discovery. All SQL for legacy WIG20 jobs lives here.

The public functions are intentionally small; they preserve the old insert-only
Stooq and source-constrained Yahoo merge behavior. No schema migration.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

import pandas as pd

YAHOO_SOURCE = "yfinance|WIG20.WA"


def index_prices_table_exists(connection: Any, *, backend: str = "snowflake") -> bool:
    """Confirm an existing authoritative table, without implicitly creating it."""
    if backend == "snowflake":
        sql = (
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME=%s"
        )
        params = ("INDEX_PRICES",)
    elif backend == "duckdb":
        sql = (
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=CURRENT_SCHEMA() AND UPPER(TABLE_NAME)=?"
        )
        params = ("INDEX_PRICES",)
    else:
        raise ValueError(f"Unsupported backend {backend!r}")
    with closing(connection.cursor()) as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return bool(row and row[0] == 1)


def upsert_index_prices(
    connection: Any, frame: pd.DataFrame, *, source: str = YAHOO_SOURCE
) -> None:
    """Preserve legacy Yahoo-only overwrite rules for INDEX_PRICES."""
    sql = """MERGE INTO "INDEX_PRICES" t USING (
        SELECT %s::VARCHAR AS symbol, TO_DATE(%s) AS dt,
               %s::FLOAT AS op, %s::FLOAT AS hi, %s::FLOAT AS lo,
               %s::FLOAT AS cl, %s::VARCHAR AS src
        ) s ON t."INDEX_SYMBOL" = s.symbol AND TO_DATE(t."DATE") = s.dt
        WHEN MATCHED AND t."SOURCE" LIKE 'yfinance|%%' THEN UPDATE SET
            t."OPEN"=s.op, t."HIGH"=s.hi, t."LOW"=s.lo,
            t."CLOSE"=s.cl, t."SOURCE"=s.src
        WHEN NOT MATCHED THEN INSERT
            ("INDEX_SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "SOURCE")
        VALUES (s.symbol, s.dt, s.op, s.hi, s.lo, s.cl, s.src)"""
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            for rec in frame.itertuples(index=False):
                cur.execute(
                    sql,
                    (
                        "WIG20",
                        rec.date.isoformat(),
                        float(rec.open),
                        float(rec.high),
                        float(rec.low),
                        float(rec.close),
                        source,
                    ),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def check_index_price_overlaps(
    connection: Any, frame: pd.DataFrame, *, tolerance: float = 0.5
) -> tuple[int, list]:
    """Compare Stooq values with the existing source without modifying data."""
    first, last = frame.iloc[0]["date"], frame.iloc[-1]["date"]
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT TO_DATE("DATE"), "CLOSE", "SOURCE" FROM "INDEX_PRICES" '
            'WHERE "INDEX_SYMBOL"=%s AND TO_DATE("DATE") BETWEEN %s AND %s',
            ("WIG20", first.isoformat(), last.isoformat()),
        )
        existing = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    mismatches = []
    for rec in frame.itertuples(index=False):
        previous = existing.get(rec.date)
        if previous is not None and (
            previous[0] is None or abs(float(previous[0]) - float(rec.close)) > tolerance
        ):
            mismatches.append((rec.date, previous[0], rec.close, previous[1]))
    return len(existing), mismatches


def insert_missing_index_prices(
    connection: Any, frame: pd.DataFrame, *, batch_size: int = 500
) -> int:
    """Stooq legacy backfill: insert missing dates only, no cross-source overwrite."""
    if frame.empty:
        return 0
    stage_ddl = """CREATE TEMP TABLE IF NOT EXISTS IKAROS_WIG20_BACKFILL_STAGE (
        TRADE_DATE DATE, OPEN FLOAT, HIGH FLOAT, LOW FLOAT, CLOSE FLOAT)"""
    insert = """INSERT INTO IKAROS_WIG20_BACKFILL_STAGE (TRADE_DATE, OPEN, HIGH, LOW, CLOSE)
        VALUES (TO_DATE(%s), %s, %s, %s, %s)"""
    merge = """MERGE INTO "INDEX_PRICES" AS t USING IKAROS_WIG20_BACKFILL_STAGE AS s
        ON t."INDEX_SYMBOL"='WIG20' AND TO_DATE(t."DATE")=s.TRADE_DATE
        WHEN NOT MATCHED THEN INSERT ("INDEX_SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "SOURCE")
        VALUES ('WIG20', s.TRADE_DATE, s.OPEN, s.HIGH, s.LOW, s.CLOSE, 'stooq|wig20|daily')"""
    rows = [
        (r.date.isoformat(), float(r.open), float(r.high), float(r.low), float(r.close))
        for r in frame.itertuples(index=False)
    ]
    with closing(connection.cursor()) as cur:
        cur.execute(stage_ddl)
        try:
            cur.execute("BEGIN")
            cur.execute("DELETE FROM IKAROS_WIG20_BACKFILL_STAGE")
            for off in range(0, len(rows), batch_size):
                cur.executemany(insert, rows[off : off + batch_size])
            cur.execute(merge)
            affected = int(cur.rowcount or 0)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return affected
