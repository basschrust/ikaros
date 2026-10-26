"""Idempotent Kafka observation persistence into existing Ikaros Snowflake tables.

The IIC historical batch checkpoint table is intentionally left unchanged:
stream consumption uses Kafka consumer-group offsets as its progress mechanism.
"""

from __future__ import annotations

from contextlib import closing
from ingestion.kafka_inbound import Observation


def pair_id(connection, symbol: str) -> int:
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT "CURRENCY_PAIR_ID" FROM "CURRENCY_PAIRS" '
            'WHERE "SYMBOL" = %s AND "IS_ACTIVE" = TRUE',
            (symbol,),
        )
        ids = [row[0] for row in cur.fetchall()]
    if len(ids) != 1:
        raise ValueError(f"Expected one active FX pair for {symbol}, found {len(ids)}")
    return int(ids[0])


def upsert_observation(connection, event: Observation) -> None:
    """Atomic single-row MERGE; duplicates are safe in a single-consumer deployment.

    Existing rows are updated to capture revised provider candles. For FX,
    don't replace derived-only candles with NULL OHLC from an incomplete feed.
    """
    d = event.day.isoformat()
    if event.dataset == "FX":
        pid = pair_id(connection, event.symbol)
        sql = """MERGE INTO "FX_RATES" t USING (
            SELECT %s::NUMBER AS id, %s::VARCHAR AS symbol,
                   TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD') AS dt,
                   %s::FLOAT AS op, %s::FLOAT AS hi, %s::FLOAT AS lo,
                   %s::FLOAT AS cl, %s::FLOAT AS vol
        ) s ON t."CURRENCY_PAIR_ID" = s.id AND TO_DATE(t."DATE") = TO_DATE(s.dt)
        WHEN MATCHED THEN UPDATE SET
            t."OPEN" = COALESCE(s.op, t."OPEN"),
            t."HIGH" = COALESCE(s.hi, t."HIGH"),
            t."LOW" = COALESCE(s.lo, t."LOW"),
            t."CLOSE" = s.cl,
            t."VOLUME" = COALESCE(s.vol, t."VOLUME")
        WHEN NOT MATCHED THEN INSERT
            ("CURRENCY_PAIR_ID", "SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
        VALUES (s.id, s.symbol, s.dt, s.op, s.hi, s.lo, s.cl, s.vol)"""
        params = (
            pid,
            event.symbol,
            d,
            event.open,
            event.high,
            event.low,
            event.close,
            event.volume,
        )
    elif event.dataset == "GPW":
        sql = """MERGE INTO "PRICES" t USING (
            SELECT %s::VARCHAR AS symbol,
                   TO_TIMESTAMP_NTZ(%s, 'YYYY-MM-DD') AS dt,
                   %s::FLOAT AS op, %s::FLOAT AS hi, %s::FLOAT AS lo,
                   %s::FLOAT AS cl, %s::FLOAT AS vol,
                   %s::FLOAT AS dividend, %s::FLOAT AS split_ratio
        ) s ON t."SYMBOL" = s.symbol AND TO_DATE(t."DATE") = TO_DATE(s.dt)
        WHEN MATCHED THEN UPDATE SET
            t."OPEN" = COALESCE(s.op, t."OPEN"),
            t."HIGH" = COALESCE(s.hi, t."HIGH"),
            t."LOW" = COALESCE(s.lo, t."LOW"),
            t."CLOSE" = s.cl,
            t."VOLUME" = COALESCE(s.vol, t."VOLUME"),
            t."DIVIDEND" = COALESCE(s.dividend, t."DIVIDEND"),
            t."SPLIT_RATIO" = COALESCE(s.split_ratio, t."SPLIT_RATIO")
        WHEN NOT MATCHED THEN INSERT
            ("SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DIVIDEND", "SPLIT_RATIO")
        VALUES (s.symbol, s.dt, s.op, s.hi, s.lo, s.cl, s.vol, s.dividend, s.split_ratio)"""
        params = (
            event.symbol,
            d,
            event.open,
            event.high,
            event.low,
            event.close,
            event.volume,
            event.dividend,
            event.split_ratio,
        )
    else:
        raise ValueError(f"Unsupported dataset: {event.dataset}")
    with closing(connection.cursor()) as cur:
        cur.execute(sql, params)
