"""Probe OpenBB coverage for explicitly supplied GPW futures tickers; never writes DB."""

import argparse
from datetime import date

from openbb import obb


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    args = p.parse_args()
    for symbol in args.symbols:
        try:
            result = obb.derivatives.futures.historical(
                symbol=symbol,
                start_date=args.start,
                end_date=args.end,
                interval="1d",
                provider="yfinance",
            )
            frame = result.to_dataframe()
            print(symbol, "rows=", len(frame), "columns=", list(frame.columns))
            if not frame.empty:
                print(frame.tail(3).to_string())
        except Exception as exc:
            print(symbol, "UNAVAILABLE:", type(exc).__name__, str(exc)[:300])


if __name__ == "__main__":
    main()
