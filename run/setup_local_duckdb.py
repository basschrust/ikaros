"""Initialize local DuckDB and register reviewed instruments, including GPW contracts.

Place reviewed GPW futures/options in data/derivative_contracts.csv and run:
    uv run python -m run.setup_local_duckdb --write

Use --contracts-csv to override the default filename. Existing registrations
and authoritative prices are not deleted or overwritten.
"""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_CONTRACTS = Path("data/derivative_contracts.csv")


def resolve_contracts_csv(*, project_root: Path, requested: Path | None) -> Path | None:
    """Use the reviewed standard manifest when present, not an example file."""
    if requested is not None:
        path = requested if requested.is_absolute() else project_root / requested
        if not path.is_file():
            raise FileNotFoundError(f"Contract manifest not found: {path}")
        return path
    default = project_root / DEFAULT_CONTRACTS
    return default if default.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contracts-csv",
        type=Path,
        help="Reviewed contracts CSV (default: data/derivative_contracts.csv if present)",
    )
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
    try:
        contracts_csv = resolve_contracts_csv(
            project_root=PROJECT_ROOT, requested=args.contracts_csv
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))

    if contracts_csv is None:
        print(
            "Derivative registry: no data/derivative_contracts.csv yet. "
            "Copy data/derivative_contracts.example.csv to that filename, "
            "review the contracts, and rerun --write."
        )
    else:
        print(f"Derivative registry: {contracts_csv}")

    with repository_from_config(config) as repo:
        report = bootstrap(
            repo.connection,
            project_root=PROJECT_ROOT,
            contracts_csv=contracts_csv,
            rates_csv=args.rates_csv,
            write=args.write,
        )
    print(f"{'SEEDED' if args.write else 'DRY_RUN'} local registry: {report}")
    if report.missing_files:
        print("Optional local equity universe files missing:")
        for value in report.missing_files:
            print(" -", value)
        print("FX pairs come from config/currencies.yaml; equities need reviewed CSV files.")
    if contracts_csv is not None and args.write:
        print(
            "Derivative provider mappings are seeded automatically. "
            "Next: uv run python -m run.update_all --datasets DERIVATIVES --write"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
