"""Refresh completed daily WIG20 index candles into Snowflake INDEX_PRICES.

Uses Yahoo WIG20.WA hourly candles aggregated to Warsaw sessions when daily
index candles are unavailable. Dry-run unless --write.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import closing

from ingestion.index_prices_refresh import (
    SOURCE,
    aggregate_hourly_index_prices,
    normalize_yahoo_index_prices,
    upsert_index_prices,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=10, help="Calendar days to recheck")
    parser.add_argument("--write", action="store_true", help="MERGE into Snowflake")
    args = parser.parse_args()
    if args.days < 2:
        parser.error("--days must be at least 2")
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    import yfinance as yf

    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    # Today is not safe for end-of-day until data finality can be verified.
    last_complete = now.date() - timedelta(days=1)
    start = last_complete - timedelta(days=args.days - 1)
    raw = yf.download(
        "WIG20.WA",
        start=start.isoformat(),
        end=(last_complete + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    frame = normalize_yahoo_index_prices(raw, start=start, end=last_complete)
    source = SOURCE
    if frame.empty:
        print("Yahoo daily WIG20 history unavailable; trying hourly session aggregation")
        hourly = yf.download(
            "WIG20.WA",
            start=start.isoformat(),
            end=(last_complete + timedelta(days=1)).isoformat(),
            interval="1h",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        frame = aggregate_hourly_index_prices(hourly, start=start, end=last_complete)
        source = "yfinance|WIG20.WA|hourly_aggregated"
    if frame.empty:
        raise RuntimeError(
            "Yahoo returned no usable WIG20 daily or hourly history. "
            "No Snowflake writes performed; source needs replacement or "
            "a licensed EoD provider. WIG20 is NOT delisted."
        )
    print(
        f"INDEX WIG20 source={source} fetched={len(frame)} start={frame.iloc[0]['date']} "
        f"end={frame.iloc[-1]['date']} write={args.write}"
    )
    if not args.write:
        print(frame.tail(5).to_string(index=False))
        return
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake as database driver in config/database.yaml")
    with repository_from_config(config) as repo:
        with closing(repo.connection.cursor()) as cur:
            cur.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=CURRENT_SCHEMA() AND TABLE_NAME=%s",
                ("INDEX_PRICES",),
            )
            if cur.fetchone()[0] != 1:
                raise RuntimeError(
                    "INDEX_PRICES not found; apply existing index-prices DDL before enabling the job"
                )
        upsert_index_prices(repo.connection, frame, source=source)
    print(f"INDEX WIG20 merged={len(frame)}")


if __name__ == "__main__":
    main()
