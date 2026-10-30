"""Stooq WIG20 daily OHLC parsing and conservative index-price persistence.

The source is an independent market data provider; check terms before bulk use.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date
from io import StringIO
import math
import pandas as pd

SOURCE = "stooq|wig20|daily"
COLUMNS = ["date", "open", "high", "low", "close"]
ALIASES = {
    "date": "date",
    "data": "date",
    "open": "open",
    "otwarcie": "open",
    "high": "high",
    "najwyzszy": "high",
    "najwyższy": "high",
    "max": "high",
    "low": "low",
    "najnizszy": "low",
    "najniższy": "low",
    "min": "low",
    "close": "close",
    "zamkniecie": "close",
    "zamknięcie": "close",
}


def parse_csv(content: str, *, start: date, end: date) -> pd.DataFrame:
    """Accept Stooq's English/Polish CSV headers; reject malformed daily OHLC."""
    if start > end:
        raise ValueError("start must be <= end")
    if not content.strip() or "no data" in content[:120].lower():
        raise ValueError("Provider returned no CSV observations")
    frame = pd.read_csv(StringIO(content.lstrip("\ufeff")), encoding="utf-8-sig")
    frame = frame.rename(columns={c: ALIASES.get(str(c).strip().lower(), c) for c in frame.columns})
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Stooq data missing columns: {sorted(missing)}")
    frame = frame[COLUMNS].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    frame = frame.loc[frame["date"].between(start, end)].copy()
    for col in COLUMNS[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if frame.empty:
        raise ValueError("No WIG20 observations inside requested dates")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate dates in provider response")
    for row in frame.itertuples(index=False):
        nums = (row.open, row.high, row.low, row.close)
        if not all(math.isfinite(float(n)) and n > 0 for n in nums):
            raise ValueError(f"Invalid prices on {row.date}")
        if (
            row.low > min(row.open, row.close)
            or row.high < max(row.open, row.close)
            or row.high < row.low
        ):
            raise ValueError(f"Inconsistent OHLC on {row.date}")
    return frame.sort_values("date").reset_index(drop=True)


def check_overlaps(conn, frame: pd.DataFrame, *, tolerance: float = 0.5) -> tuple[int, list]:
    """Check closings against existing index records; abort on material disagreement."""
    first, last = frame.iloc[0]["date"], frame.iloc[-1]["date"]
    with closing(conn.cursor()) as cur:
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


def insert_missing(conn, frame: pd.DataFrame, *, batch_size: int = 500) -> int:
    """Stage and merge only previously missing WIG20 dates, without changing other sources."""
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
    with closing(conn.cursor()) as cur:
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
