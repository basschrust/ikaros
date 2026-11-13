"""Capture Yahoo US-equity option chains into a distinct Snowflake snapshot table.

This is NOT an historical OHLCV backfill and does not update DERIVATIVE_PRICES.
Underlying must already be registered with VENUE='US'. Dry-run by default.

Example:
  uv run --extra snowflake python -m run.collect_us_option_chains --symbols AAPL MSFT
  uv run --extra snowflake python -m run.collect_us_option_chains --symbols AAPL MSFT --write
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import logging
import math
import re
import time

import pandas as pd
from snowflake.connector import SnowflakeConnection

LOG = logging.getLogger(__name__)
OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
FIELDS = (
    "underlying",
    "contract_symbol",
    "expiry_date",
    "option_side",
    "strike",
    "currency",
    "quote_date",
    "captured_at",
    "last_trade_at",
    "bid",
    "ask",
    "last_price",
    "volume",
    "open_interest",
    "implied_volatility",
    "source",
)
DDL = """CREATE TABLE IF NOT EXISTS "US_OPTION_CHAIN_SNAPSHOTS" (
    "UNDERLYING" VARCHAR NOT NULL,
    "CONTRACT_SYMBOL" VARCHAR NOT NULL,
    "EXPIRY_DATE" DATE NOT NULL,
    "OPTION_SIDE" VARCHAR NOT NULL,
    "STRIKE" FLOAT NOT NULL,
    "CURRENCY" VARCHAR NOT NULL,
    "QUOTE_DATE" DATE NOT NULL,
    "CAPTURED_AT" TIMESTAMP_NTZ NOT NULL,
    "LAST_TRADE_AT" TIMESTAMP_NTZ,
    "BID" FLOAT, "ASK" FLOAT, "LAST_PRICE" FLOAT,
    "VOLUME" FLOAT, "OPEN_INTEREST" FLOAT, "IMPLIED_VOLATILITY" FLOAT,
    "SOURCE" VARCHAR NOT NULL
)"""
STAGE = 'CREATE TEMP TABLE IF NOT EXISTS "IKAROS_US_OPTION_SNAPSHOT_STAGE" LIKE "US_OPTION_CHAIN_SNAPSHOTS"'
MERGE = """MERGE INTO "US_OPTION_CHAIN_SNAPSHOTS" t
    USING "IKAROS_US_OPTION_SNAPSHOT_STAGE" s
    ON t."CONTRACT_SYMBOL" = s."CONTRACT_SYMBOL" AND t."QUOTE_DATE" = s."QUOTE_DATE"
    WHEN MATCHED THEN UPDATE SET
      t."CAPTURED_AT"=s."CAPTURED_AT", t."LAST_TRADE_AT"=s."LAST_TRADE_AT",
      t."BID"=s."BID", t."ASK"=s."ASK", t."LAST_PRICE"=s."LAST_PRICE",
      t."VOLUME"=s."VOLUME", t."OPEN_INTEREST"=s."OPEN_INTEREST",
      t."IMPLIED_VOLATILITY"=s."IMPLIED_VOLATILITY", t."SOURCE"=s."SOURCE"
    WHEN NOT MATCHED THEN INSERT (
      "UNDERLYING", "CONTRACT_SYMBOL", "EXPIRY_DATE", "OPTION_SIDE", "STRIKE",
      "CURRENCY", "QUOTE_DATE", "CAPTURED_AT", "LAST_TRADE_AT", "BID", "ASK",
      "LAST_PRICE", "VOLUME", "OPEN_INTEREST", "IMPLIED_VOLATILITY", "SOURCE"
    ) VALUES (
      s."UNDERLYING", s."CONTRACT_SYMBOL", s."EXPIRY_DATE", s."OPTION_SIDE",
      s."STRIKE", s."CURRENCY", s."QUOTE_DATE", s."CAPTURED_AT", s."LAST_TRADE_AT",
      s."BID", s."ASK", s."LAST_PRICE", s."VOLUME", s."OPEN_INTEREST",
      s."IMPLIED_VOLATILITY", s."SOURCE"
    )"""


def optional_number(value: object, field: str) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid {field}: {value!r}")
    return number


def naive_utc(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").tz_localize(None).to_pydatetime()


def normalize_chain(
    underlying: str, expiry: date, side: str, frame: pd.DataFrame, captured_at: datetime
) -> list[dict]:
    """Normalize raw Yahoo chain; reject contract/expiry inconsistencies.

    OCC root can differ from current underlying for adjusted contracts; those are
    rejected rather than silently assuming the standard 100-share deliverable.
    """
    if side not in {"CALL", "PUT"}:
        raise ValueError("Expected CALL or PUT")
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    now = captured_at.astimezone(timezone.utc).replace(tzinfo=None)
    result = []
    for rec in frame.to_dict("records"):
        sym = str(rec.get("contractSymbol", ""))
        match = OCC.fullmatch(sym)
        if not match:
            raise ValueError(f"Invalid OCC contract symbol: {sym!r}")
        root, expcode, cp, strikecode = match.groups()
        parsed_exp = datetime.strptime(expcode, "%y%m%d").date()
        parsed_strike = int(strikecode) / 1000
        if root != underlying or parsed_exp != expiry or cp != side[0]:
            raise ValueError(f"Unexpected/adjusted option contract: {sym}")
        strike = optional_number(rec.get("strike"), "strike")
        if strike is None or strike <= 0 or not math.isclose(strike, parsed_strike, abs_tol=1e-6):
            raise ValueError(f"Strike mismatch: {sym}")
        last_trade = naive_utc(rec.get("lastTradeDate"))
        if last_trade is not None and last_trade > now:
            raise ValueError(f"Future lastTradeDate: {sym}")
        row = {
            "underlying": underlying,
            "contract_symbol": sym,
            "expiry_date": expiry,
            "option_side": side,
            "strike": strike,
            "currency": "USD",
            "quote_date": captured_at.astimezone(timezone.utc).date(),
            "captured_at": now,
            "last_trade_at": last_trade,
            "bid": optional_number(rec.get("bid"), "bid"),
            "ask": optional_number(rec.get("ask"), "ask"),
            "last_price": optional_number(rec.get("lastPrice"), "lastPrice"),
            "volume": optional_number(rec.get("volume"), "volume"),
            "open_interest": optional_number(rec.get("openInterest"), "openInterest"),
            "implied_volatility": optional_number(
                rec.get("impliedVolatility"), "impliedVolatility"
            ),
            "source": "yfinance|option_chain",
        }
        if row["bid"] is not None and row["ask"] is not None and row["bid"] > row["ask"]:
            # Crossed or stale markets should not be treated as validated quotes.
            LOG.warning("CROSSED MARKET %s bid=%s ask=%s", sym, row["bid"], row["ask"])
        result.append(row)
    if len({r["contract_symbol"] for r in result}) != len(result):
        raise ValueError("Duplicate option symbols in provider response")
    return result


def fetch_chains(
    symbol: str, *, max_expirations: int = 2, asof: datetime | None = None
) -> list[dict]:
    import yfinance as yf

    if not re.fullmatch(r"[A-Z]{1,5}", symbol):
        raise ValueError("First US pilot accepts simple unadjusted US underlying tickers only")
    ticker = yf.Ticker(symbol)
    expiries = list(ticker.options)[:max_expirations]
    if not expiries:
        raise RuntimeError(f"No option expirations returned for {symbol}")
    captured = asof or datetime.now(timezone.utc)
    out = []
    for expiration in expiries:
        expiry = date.fromisoformat(expiration)
        chain = ticker.option_chain(expiration)
        out.extend(normalize_chain(symbol, expiry, "CALL", chain.calls, captured))
        out.extend(normalize_chain(symbol, expiry, "PUT", chain.puts, captured))
    if not out:
        raise RuntimeError(f"No option contracts returned for {symbol}")
    if len({r["contract_symbol"] for r in out}) != len(out):
        raise RuntimeError(f"Duplicate symbols across expirations for {symbol}")
    return out


def registered_underlyings(connection: SnowflakeConnection, symbols: list[str]) -> None:
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT "SYMBOL", "CURRENCY" FROM "SECURITIES" WHERE "VENUE"=%s '
            'AND "SYMBOL" IN (' + ",".join(["%s"] * len(symbols)) + ")",
            ("US", *symbols),
        )
        found = cur.fetchall()
    by_symbol = {str(sym): ccy for sym, ccy in found}
    missing = set(symbols) - set(by_symbol)
    if missing:
        raise ValueError(f"US underlyings not registered: {sorted(missing)}")
    if any(ccy != "USD" for ccy in by_symbol.values()):
        raise ValueError("US option underlyings must be registered in USD")


def persist(connection: SnowflakeConnection, rows: list[dict]) -> None:
    if not rows:
        return
    if len({(r["contract_symbol"], r["quote_date"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate contract/date in snapshot")
    insert = (
        'INSERT INTO "IKAROS_US_OPTION_SNAPSHOT_STAGE" ('
        + ",".join(f'"{x.upper()}"' for x in FIELDS)
        + ") VALUES ("
        + ",".join(["%s"] * len(FIELDS))
        + ")"
    )
    params = [tuple(r[field] for field in FIELDS) for r in rows]
    with closing(connection.cursor()) as cur:
        cur.execute(DDL)
        cur.execute(STAGE)
        try:
            cur.execute("BEGIN")
            cur.execute('DELETE FROM "IKAROS_US_OPTION_SNAPSHOT_STAGE"')
            cur.executemany(insert, params)
            cur.execute(MERGE)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--max-expirations", type=int, default=2)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.max_expirations < 1 or args.delay < 0:
        parser.error("max-expirations must be positive and delay nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    if args.write:
        from config.config import load_config
        from database.repositories.factory import repository_from_config

        cfg = load_config("database.yaml")
        if cfg.get("database", {}).get("driver", "").lower() != "snowflake":
            parser.error("Snowflake required for --write")
        with repository_from_config(cfg) as repo:
            registered_underlyings(repo.connection, symbols)
            for idx, sym in enumerate(symbols):
                if idx and args.delay:
                    time.sleep(args.delay)
                rows = fetch_chains(sym, max_expirations=args.max_expirations)
                persist(repo.connection, rows)
                LOG.info("%s: stored %d option snapshots", sym, len(rows))
    else:
        for idx, sym in enumerate(symbols):
            if idx and args.delay:
                time.sleep(args.delay)
            rows = fetch_chains(sym, max_expirations=args.max_expirations)
            LOG.info("DRY RUN %s: %d contracts; no Snowflake writes", sym, len(rows))


if __name__ == "__main__":
    main()
