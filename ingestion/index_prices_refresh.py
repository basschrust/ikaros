"""Normalize Yahoo WIG20 candles and upsert source-tagged index OHLC."""

from __future__ import annotations

from contextlib import closing
from datetime import date
import math
import pandas as pd

SOURCE = "yfinance|WIG20.WA"


def normalize_yahoo_index_prices(raw: pd.DataFrame, *, start: date, end: date) -> pd.DataFrame:
    columns = ["date", "open", "high", "low", "close"]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    data = raw.copy()
    if isinstance(data.columns, pd.MultiIndex):
        # yfinance >=0.2 frequently returns (Price, Ticker).
        data.columns = [str(item[0]).lower() for item in data.columns]
    else:
        data.columns = [str(c).lower() for c in data.columns]
    data = data.reset_index()
    data.columns = [str(c).lower() for c in data.columns]
    if "date" not in data.columns and "datetime" in data.columns:
        data = data.rename(columns={"datetime": "date"})
    missing = set(columns) - set(data.columns)
    if missing:
        raise ValueError(f"Missing WIG20 fields: {sorted(missing)}")
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.date
    data = data.loc[data["date"].between(start, end), columns].copy()
    for key in columns[1:]:
        data[key] = pd.to_numeric(data[key], errors="raise")
    for rec in data.itertuples(index=False):
        if not all(
            math.isfinite(float(getattr(rec, c))) and float(getattr(rec, c)) > 0
            for c in columns[1:]
        ):
            raise ValueError(f"Invalid WIG20 OHLC on {rec.date}")
        if rec.high < max(rec.open, rec.close, rec.low) or rec.low > min(
            rec.open, rec.close, rec.high
        ):
            raise ValueError(f"Inconsistent WIG20 OHLC on {rec.date}")
    if data["date"].duplicated().any():
        raise ValueError("Duplicate WIG20 dates returned by provider")
    return data.sort_values("date").reset_index(drop=True)


def upsert_index_prices(connection, frame: pd.DataFrame, *, source: str = SOURCE) -> None:
    """Upsert INDEX_PRICES; keep previously imported non-Yahoo EoD values.

    The table is assumed to be the index-price table already installed in Ikaros,
    with INDEX_SYMBOL, DATE, OPEN/HIGH/LOW/CLOSE and SOURCE columns.
    """
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


def aggregate_hourly_index_prices(raw: pd.DataFrame, *, start: date, end: date) -> pd.DataFrame:
    """Construct session OHLC from hourly WIG20 candles using Europe/Warsaw dates.

    Requires tz-aware timestamps so UTC rows cannot silently shift sessions.
    This is a fallback for unavailable Yahoo daily index candles, not an
    exchange-published official daily OHLC feed.
    """
    from zoneinfo import ZoneInfo

    names = ["date", "open", "high", "low", "close"]
    if raw.empty:
        return pd.DataFrame(columns=names)
    data = raw.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [str(col[0]).lower() for col in data.columns]
    else:
        data.columns = [str(col).lower() for col in data.columns]
    data = data.reset_index()
    data.columns = [str(col).lower() for col in data.columns]
    stamp_column = next((key for key in ("datetime", "date") if key in data), None)
    if stamp_column is None or any(col not in data for col in names[1:]):
        raise ValueError("Hourly WIG20 response missing timestamp or OHLC columns")
    stamps = pd.to_datetime(data[stamp_column], errors="raise")
    if stamps.dt.tz is None:
        raise ValueError("Hourly WIG20 timestamps lack timezone; refusing unsafe session grouping")
    data["date"] = stamps.dt.tz_convert(ZoneInfo("Europe/Warsaw")).dt.date
    data["_stamp"] = stamps
    data = data.loc[data["date"].between(start, end)].sort_values("_stamp").copy()
    if data.empty:
        return pd.DataFrame(columns=names)
    for field in names[1:]:
        data[field] = pd.to_numeric(data[field], errors="raise")
    if data["_stamp"].duplicated().any():
        raise ValueError("Duplicate hourly WIG20 timestamps")
    if not data[names[1:]].notna().all().all():
        raise ValueError("Missing hourly WIG20 OHLC values")
    output = data.groupby("date", as_index=False).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        bar_count=("_stamp", "size"),
    )
    # Missing hourly bars may produce a misleading close. Require meaningful coverage.
    output = output.loc[output["bar_count"] >= 5].drop(columns="bar_count")
    return normalize_yahoo_index_prices(output.set_index("date"), start=start, end=end)
