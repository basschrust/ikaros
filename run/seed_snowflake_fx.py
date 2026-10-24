"""Seed Snowflake currency reference data from config/currencies.yaml.

Run from the Ikaros project root:
    uv run python -m run.seed_snowflake_fx --symbol "EURPLN=X"
    uv run python -m run.seed_snowflake_fx --symbol "EURPLN=X" --write

Dry-run by default. Inserts only missing currencies/pairs, in one transaction.
"""

from __future__ import annotations

import argparse
from contextlib import closing

from config.config import load_config
from database.repositories.factory import repository_from_config


def prepare_seed(config: dict, symbol: str):
    pairs = [p for p in config.get("currency_pairs", []) if p["symbol"] == symbol]
    if len(pairs) != 1:
        raise ValueError(f"Expected one {symbol!r} entry in currencies.yaml; found {len(pairs)}")
    pair = pairs[0]
    currencies = {c["code"]: c for c in config.get("currencies", [])}
    base, quote = pair["base"], pair["quote"]
    for code in (base, quote):
        if code not in currencies:
            raise ValueError(f"Currency {code!r} is not defined in currencies.yaml")
    if base == quote:
        raise ValueError("Base and quote currencies must differ")
    return pair, [currencies[base], currencies[quote]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="EURPLN=X")
    parser.add_argument("--write", action="store_true", help="Insert the missing reference rows")
    args = parser.parse_args()

    settings = load_config("database.yaml")
    if settings.get("database", {}).get("driver", "").lower() != "snowflake":
        raise SystemExit("Set database.driver: snowflake in config/database.yaml first")

    fx_config = load_config("currencies.yaml")
    pair, currencies = prepare_seed(fx_config, args.symbol)

    with repository_from_config(settings) as repo:
        # Create tables if missing, as implemented in SnowflakeRepository.
        with closing(repo.connection.cursor()) as cur:
            cur.execute(
                'SELECT "CURRENCY_ID", "CODE" FROM "CURRENCIES" WHERE "CODE" IN (%s, %s)',
                (pair["base"], pair["quote"]),
            )
            existing = {code: int(cid) for cid, code in cur.fetchall()}
            for c in currencies:
                print(
                    f"Currency {c['code']}: {'already present' if c['code'] in existing else 'will insert'}"
                )

            cur.execute(
                'SELECT "CURRENCY_PAIR_ID", "BASE_CURRENCY_ID", "QUOTE_CURRENCY_ID" '
                'FROM "CURRENCY_PAIRS" WHERE "SYMBOL" = %s',
                (args.symbol,),
            )
            found = cur.fetchall()
            if len(found) > 1:
                raise RuntimeError(f"Multiple rows already exist for {args.symbol}")
            if found:
                print(f"Pair {args.symbol}: already present (ID {found[0][0]})")
                if any(c["code"] not in existing for c in currencies):
                    raise RuntimeError("Pair exists but one of its currencies is missing")
                if (int(found[0][1]), int(found[0][2])) != (
                    existing[pair["base"]],
                    existing[pair["quote"]],
                ):
                    raise RuntimeError("Existing pair points at the wrong currencies")
                print("PASS: reference data exists; rerun FX smoke ingestion.")
                return

            print(f"Pair {args.symbol}: will insert")
            if not args.write:
                print("DRY RUN: no changes made. Use --write to seed.")
                return

            # Single Snowflake DML transaction; any SQL failure rolls back both tables.
            # Intended for one setup process at a time, not concurrent seeders.
            try:
                cur.execute("BEGIN")
                cur.execute('SELECT COALESCE(MAX("CURRENCY_ID"), 0) FROM "CURRENCIES"')
                next_currency = int(cur.fetchone()[0])
                for currency in currencies:
                    code = currency["code"]
                    if code not in existing:
                        next_currency += 1
                        cur.execute(
                            'INSERT INTO "CURRENCIES" '
                            '("CURRENCY_ID", "CODE", "NAME", "MINOR_UNIT", "IS_ACTIVE") '
                            "VALUES (%s, %s, %s, %s, TRUE)",
                            (next_currency, code, currency["name"], currency["minor_unit"]),
                        )
                        existing[code] = next_currency
                cur.execute('SELECT COALESCE(MAX("CURRENCY_PAIR_ID"), 0) FROM "CURRENCY_PAIRS"')
                next_pair = int(cur.fetchone()[0]) + 1
                cur.execute(
                    'INSERT INTO "CURRENCY_PAIRS" '
                    '("CURRENCY_PAIR_ID", "BASE_CURRENCY_ID", "QUOTE_CURRENCY_ID", '
                    '"SYMBOL", "IS_ACTIVE") VALUES (%s, %s, %s, %s, TRUE)',
                    (next_pair, existing[pair["base"]], existing[pair["quote"]], args.symbol),
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

            cur.execute(
                'SELECT "CURRENCY_PAIR_ID" FROM "CURRENCY_PAIRS" WHERE "SYMBOL" = %s',
                (args.symbol,),
            )
            result = cur.fetchall()
            if len(result) != 1:
                raise RuntimeError("Post-write verification failed")
            print(f"PASS: {args.symbol} is registered as currency_pair_id={result[0][0]}")


if __name__ == "__main__":
    main()
