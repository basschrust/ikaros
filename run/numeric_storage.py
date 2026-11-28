"""User CLI for read-only numeric audit and explicit backed-up DuckDB migration."""

from __future__ import annotations

import argparse
from pathlib import Path

from database.numeric_migration import UnsafeNumericMigration, inspect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Exact path to ikaros.duckdb")
    parser.add_argument("--write", action="store_true", help="Backup and migrate (default audit)")
    parser.add_argument("--report", type=Path, help="Optional JSON audit output path")
    args = parser.parse_args(argv)
    try:
        return inspect(args.database, write=args.write, report=args.report)
    except (OSError, ValueError, UnsafeNumericMigration) as exc:
        print(f"NUMERIC STORAGE: {exc}")
        return 2
    except Exception as exc:
        print(f"NUMERIC STORAGE INCOMPLETE ({type(exc).__name__}): {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
