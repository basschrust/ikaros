"""Prepare a local-only Ikaros database and optional reviewed instrument files."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-csv", type=Path)
    parser.add_argument("--rates-csv", type=Path)
    parser.add_argument(
        "--write", action="store_true", help="Seed local registry (dry-run by default)"
    )
    args = parser.parse_args(argv)
    from config.config import PROJECT_ROOT, load_config
    from database.local_bootstrap import bootstrap
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver") != "duckdb":
        parser.error("Local bootstrap requires IKAROS_DB_DRIVER=duckdb")
    with repository_from_config(config) as repo:
        report = bootstrap(
            repo.connection,
            project_root=PROJECT_ROOT,
            contracts_csv=args.contracts_csv,
            rates_csv=args.rates_csv,
            write=args.write,
        )
    print(f"{'SEEDED' if args.write else 'DRY_RUN'} local registry: {report}")
    if report.missing_files:
        print("Optional local universe files missing:")
        for value in report.missing_files:
            print(" -", value)
        print("FX universe comes from config/currencies.yaml; equities need reviewed CSV files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
