"""Convert a saved contract-history HTML page into the existing ingestion CSV schema.

Never performs network requests. CSV is validated using ingestion.derivatives.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from ingestion.derivatives import validate_contracts, validate_prices
from providers.derivatives.biznesradar import parse_history_html


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--contracts", required=True, type=Path,
                        help="Verified contract specification CSV, same input as existing importer")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    contracts = validate_contracts(pd.read_csv(args.contracts, dtype={"contract_id": str}))
    matches = contracts.loc[contracts["symbol"] == args.symbol]
    if len(matches) != 1:
        parser.error(f"Expected one contract for symbol {args.symbol} in {args.contracts}")
    contract_id = str(matches.iloc[0]["contract_id"])
    frame = parse_history_html(args.html.read_text(encoding="utf-8"), symbol=args.symbol,
                               start=args.start, end=args.end)
    output = pd.DataFrame({
        "contract_id": contract_id,
        "date": frame["date"],
        "open": frame["open"],
        "high": frame["high"],
        "low": frame["low"],
        "close": frame["close"],
        "volume": frame["volume"],
        "open_interest": None,
        "settlement": None,
        "source": "biznesradar-html-snapshot",
    })
    output = validate_prices(output, {contract_id})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.out, index=False)
    print(f"Wrote {len(output)} validated observations to {args.out}")
    print(f"First={output['date'].min()} Last={output['date'].max()}")


if __name__ == "__main__":
    main()
