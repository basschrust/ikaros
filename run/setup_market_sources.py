"""Initialize WIG20/derivative multi-source candidate schema and seed aliases.

    uv run --extra snowflake python -m run.setup_market_sources --write

Dry run merely describes the plan; --write performs additive DDL and seeds
aliases from registered contracts. Never creates or rewrites contracts.
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Apply DDL and seed mappings")
    args = parser.parse_args(argv)
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    settings = load_config("database.yaml")
    backend = settings.get("database", {}).get("driver", "").lower()
    if backend not in ("snowflake", "duckdb"):
        parser.error("Select Snowflake or DuckDB backend")
    if not args.write:
        print(
            "DRY RUN: will create INDEX/DERIVATIVE_PROVIDER_SYMBOLS, "
            "INDEX/DERIVATIVE_PRICE_CANDIDATES and sequences; "
            "extend existing authoritative price tables and seed reviewed mappings. "
            "Run with --write to apply."
        )
        return 0
    from database.market_quote_schema import ensure_market_quote_schema
    from database.market_quote_seed import seed_mappings

    with repository_from_config(settings) as repo:
        ensure_market_quote_schema(repo.connection, backend=backend)
        try:
            plan = seed_mappings(repo.connection, backend=backend, write=False)
        except Exception:
            raise
        print("To add mappings:", plan)
        seeded = seed_mappings(repo.connection, backend=backend, write=True)
    print("Market sources ready; new mappings:", seeded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
