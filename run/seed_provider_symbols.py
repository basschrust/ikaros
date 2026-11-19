"""Register GPW provider-symbol mappings from canonical SECURITIES.

Dry run (no database modification):
  uv run --extra snowflake python -m run.seed_provider_symbols

Include reviewed third-party mappings and write:
  uv run --extra snowflake python -m run.seed_provider_symbols \
    --csv data/provider_symbols_gpw_reviewed.csv --write
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", choices=("GPW",), default="GPW")
    parser.add_argument("--csv", type=Path, help="Reviewed provider-specific symbol mappings")
    parser.add_argument("--write", action="store_true", help="Persist changes (default: dry run)")
    parser.add_argument(
        "--skip-unregistered",
        action="store_true",
        help="Warn and skip CSV rows missing from SECURITIES (no guessed identity)",
    )
    parser.add_argument(
        "--activate-alternatives",
        action="store_true",
        help="Enable Stooq and BiznesRadar after reviewed symbol mapping",
    )
    args = parser.parse_args(argv)
    if args.csv is not None and not args.csv.is_file():
        parser.error(f"Mapping CSV missing: {args.csv}")

    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from database.provider_symbols_seed import seed_provider_symbols

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        parser.error("Select Snowflake in config/database.yaml")
    with repository_from_config(config) as repo:
        plan = seed_provider_symbols(
            repo.connection,
            venue=args.venue,
            csv_path=args.csv,
            write=args.write,
            activate_alternatives=args.activate_alternatives,
            skip_unregistered=args.skip_unregistered,
        )
    print(
        f"{'WRITTEN' if args.write else 'DRY RUN'}: "
        f"new_providers={len(plan.providers)} new_mappings={len(plan.mappings)} "
        f"existing_mappings={plan.existing_count} "
        f"activated_providers={len(plan.activate_ids)}"
    )
    if plan.mappings:
        for _, mapping in plan.mappings[:10]:
            print(
                f"  {mapping.asset_id}: {mapping.provider_code} "
                f"{mapping.provider_symbol} from {mapping.valid_from}"
            )
        if len(plan.mappings) > 10:
            print(f"  ... and {len(plan.mappings) - 10} further mappings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
