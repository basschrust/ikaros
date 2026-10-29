"""Register discovered FW20 futures series without fabricating contract quotes.

Example: uv run --extra snowflake python -m run.register_fw20_contracts --write
"""

from __future__ import annotations

import argparse
from datetime import date
from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.fw20_registry import register


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    p.add_argument("--write", action="store_true", help="Register verified WIG20 series")
    args = p.parse_args()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("config/database.yaml must select snowflake")
    with repository_from_config(config) as repo:
        result = register(repo.connection, as_of=args.as_of, write=args.write)
    for symbol, status in result:
        print(symbol, status)
    print(f"Processed {len(result)} FW20 candidates, write={args.write}")
    if any(status in ("CONFLICT", "DUPLICATE_EXISTING") for _, status in result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
