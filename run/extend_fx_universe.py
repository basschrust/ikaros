"""Merge FX expansion into config/currencies.yaml; dry-run unless --write."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml


def merge_universe(existing: dict, expansion: dict) -> tuple[dict, dict[str, int]]:
    result = dict(existing)
    old_currencies = [dict(item) for item in existing.get("currencies", [])]
    old_pairs = [dict(item) for item in existing.get("currency_pairs", [])]
    additions = expansion.get("currencies", [])
    groups = expansion.get("backfill_groups", {})
    all_symbols = list(dict.fromkeys(symbol for items in groups.values() for symbol in items))

    def by_key(rows: list[dict], key: str) -> dict:
        keys = [row[key] for row in rows]
        repeated = [value for value, count in Counter(keys).items() if count > 1]
        if repeated:
            raise ValueError(f"Duplicate {key}: {repeated}")
        return {row[key]: row for row in rows}

    currency_by_code = by_key(old_currencies, "code")
    pair_by_symbol = by_key(old_pairs, "symbol")
    by_key(additions, "code")
    added_currencies = added_pairs = 0
    for item in additions:
        code = item["code"]
        if code in currency_by_code:
            current = currency_by_code[code]
            if (current.get("name"), current.get("minor_unit")) != (
                item["name"],
                item["minor_unit"],
            ):
                raise ValueError(f"Conflicting currency: {code}")
            continue
        currency_by_code[code] = item
        old_currencies.append(dict(item))
        added_currencies += 1
    for symbol in all_symbols:
        if len(symbol) != 8 or not symbol.endswith("=X"):
            raise ValueError(f"Malformed Yahoo currency pair: {symbol}")
        base, quote = symbol[:3], symbol[3:6]
        if base not in currency_by_code or quote not in currency_by_code or base == quote:
            raise ValueError(f"Unknown or identical currency in {symbol}")
        if symbol in pair_by_symbol:
            p = pair_by_symbol[symbol]
            if (p["base"], p["quote"]) != (base, quote):
                raise ValueError(f"Conflicting pair: {symbol}")
            continue
        item = {"base": base, "quote": quote, "symbol": symbol}
        old_pairs.append(item)
        pair_by_symbol[symbol] = item
        added_pairs += 1
    result["currencies"] = old_currencies
    result["currency_pairs"] = old_pairs
    return result, {"currencies": added_currencies, "pairs": added_pairs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("config/currencies.yaml"))
    parser.add_argument("--expansion", type=Path, default=Path("config/fx_expansion.yaml"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    original = args.source.read_text(encoding="utf-8")
    current = yaml.safe_load(original)
    extension = yaml.safe_load(args.expansion.read_text(encoding="utf-8"))
    merged, added = merge_universe(current, extension)
    print(f"Would add {added['currencies']} currencies and {added['pairs']} pairs")
    if not args.write:
        print("DRY RUN: no files changed")
        return
    if not any(added.values()):
        print("Already up to date")
        return
    backup = args.source.with_name(
        f"{args.source.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    backup.write_text(original, encoding="utf-8")
    args.source.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    print(f"Updated {args.source}, backed up to {backup}")


if __name__ == "__main__":
    main()
