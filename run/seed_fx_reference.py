"""Idempotent seed of currencies and FX pairs from config/currencies.yaml.

Dry-run by default. Run from project root:
    uv run python -m run.seed_fx_reference --symbols USDPLN=X EURUSD=X
    uv run python -m run.seed_fx_reference --symbols USDPLN=X EURUSD=X --write
    uv run python -m run.seed_fx_reference --all --write
"""

from __future__ import annotations

import argparse
import logging
from contextlib import closing
from typing import Any

from config.config import load_config
from database.repositories.factory import repository_from_config

log = logging.getLogger("ikaros.seed_fx")


def select_reference(
    config: dict[str, Any], symbols: list[str] | None
) -> tuple[list[dict], list[dict]]:
    currencies = config.get("currencies", [])
    pairs = config.get("currency_pairs", [])
    by_code = {c["code"]: c for c in currencies}
    by_symbol = {p["symbol"]: p for p in pairs}
    if len(by_code) != len(currencies):
        raise ValueError("Duplicate currency codes in configuration")
    if len(by_symbol) != len(pairs):
        raise ValueError("Duplicate pair symbols in configuration")
    if symbols is None:
        selected = pairs
    else:
        unknown = set(symbols) - set(by_symbol)
        if unknown:
            raise ValueError(f"Unknown symbols in currencies.yaml: {sorted(unknown)}")
        selected = [by_symbol[s] for s in dict.fromkeys(symbols)]
    required_codes = {code for p in selected for code in (p["base"], p["quote"])}
    unknown_codes = required_codes - set(by_code)
    if unknown_codes:
        raise ValueError(f"Missing currencies in YAML: {sorted(unknown_codes)}")
    if any(p["base"] == p["quote"] for p in selected):
        raise ValueError("A currency pair cannot use the same currency twice")
    return [c for c in currencies if c["code"] in required_codes], selected


def _fetch_reference(cur):
    cur.execute('SELECT "CURRENCY_ID", "CODE" FROM "CURRENCIES"')
    rows = cur.fetchall()
    ids: dict[str, int] = {}
    used_ids = set()
    for currency_id, code in rows:
        if code in ids or currency_id in used_ids:
            raise ValueError(f"Duplicate Snowflake currency ID or code: {code}")
        ids[code] = int(currency_id)
        used_ids.add(currency_id)

    cur.execute(
        'SELECT "CURRENCY_PAIR_ID", "SYMBOL", "BASE_CURRENCY_ID", "QUOTE_CURRENCY_ID" FROM "CURRENCY_PAIRS"'
    )
    existing_pairs: dict[str, tuple[int, int, int]] = {}
    used_pair_ids = set()
    for pair_id, symbol, base_id, quote_id in cur.fetchall():
        if symbol in existing_pairs or pair_id in used_pair_ids:
            raise ValueError(f"Duplicate Snowflake pair ID or symbol: {symbol}")
        existing_pairs[symbol] = (int(pair_id), int(base_id), int(quote_id))
        used_pair_ids.add(pair_id)
    return ids, existing_pairs


def seed(
    connection, currencies: list[dict], pairs: list[dict], write: bool = False
) -> tuple[int, int]:
    """Seed missing reference rows in one DML transaction. Returns counts of new rows."""
    with closing(connection.cursor()) as cur:
        ids, existing_pairs = _fetch_reference(cur)
        planned_codes = [c["code"] for c in currencies if c["code"] not in ids]
        missing_pairs = [p for p in pairs if p["symbol"] not in existing_pairs]
        # Check old symbol bindings before adding any new data.
        by_symbol = {p["symbol"]: p for p in pairs}
        for symbol, (_pid, base_id, quote_id) in existing_pairs.items():
            if symbol not in by_symbol:
                continue
            p = by_symbol[symbol]
            if p["base"] not in ids or p["quote"] not in ids:
                raise ValueError(f"Existing pair {symbol} references a missing configured currency")
            if (base_id, quote_id) != (ids[p["base"]], ids[p["quote"]]):
                raise ValueError(f"Pair {symbol} points at different currencies in Snowflake")

        log.info("New currencies: %s", planned_codes or "none")
        log.info("New pairs: %s", [p["symbol"] for p in missing_pairs] or "none")
        if not write:
            log.info("DRY RUN: no database writes; append --write to apply")
            return len(planned_codes), len(missing_pairs)
        if not planned_codes and not missing_pairs:
            log.info("Already initialized; no writes needed")
            return 0, 0
        # This initializer assumes a single seeding process. MAX+1 is NOT a
        # concurrency-safe ID allocator; use a Snowflake sequence for production.
        try:
            cur.execute("BEGIN")
            next_currency_id = max(ids.values(), default=0)
            for item in currencies:
                if item["code"] in ids:
                    continue
                next_currency_id += 1
                cur.execute(
                    'INSERT INTO "CURRENCIES" ("CURRENCY_ID", "CODE", "NAME", "MINOR_UNIT", "IS_ACTIVE") VALUES (%s, %s, %s, %s, TRUE)',
                    (next_currency_id, item["code"], item["name"], item.get("minor_unit", 2)),
                )
                ids[item["code"]] = next_currency_id
            next_pair_id = max((v[0] for v in existing_pairs.values()), default=0)
            for item in missing_pairs:
                next_pair_id += 1
                cur.execute(
                    'INSERT INTO "CURRENCY_PAIRS" ("CURRENCY_PAIR_ID", "BASE_CURRENCY_ID", "QUOTE_CURRENCY_ID", "SYMBOL", "IS_ACTIVE") VALUES (%s, %s, %s, %s, TRUE)',
                    (next_pair_id, ids[item["base"]], ids[item["quote"]], item["symbol"]),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        verified_ids, verified_pairs = _fetch_reference(cur)
        for item in currencies:
            if item["code"] not in verified_ids:
                raise RuntimeError(f"Currency not persisted: {item['code']}")
        for item in pairs:
            pair = verified_pairs.get(item["symbol"])
            if pair is None or (pair[1], pair[2]) != (
                verified_ids[item["base"]],
                verified_ids[item["quote"]],
            ):
                raise RuntimeError(f"Pair not persisted correctly: {item['symbol']}")
        log.info("PASS: %d currencies and %d pairs created", len(planned_codes), len(missing_pairs))
        return len(planned_codes), len(missing_pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--symbols", nargs="+", help="Configured Yahoo Finance pair symbols")
    selection.add_argument("--all", action="store_true", help="All configured FX pairs")
    parser.add_argument("--write", action="store_true", help="Perform inserts (otherwise dry run)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_config("database.yaml")
    if settings.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select database.driver: snowflake in config/database.yaml")
    currencies, pairs = select_reference(
        load_config("currencies.yaml"), None if args.all else args.symbols
    )
    repo = repository_from_config(settings)
    try:
        seed(repo.connection, currencies, pairs, write=args.write)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
