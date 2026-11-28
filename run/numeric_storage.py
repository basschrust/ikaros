"""User CLI for read-only numeric audit and explicit backed-up DuckDB migration."""

from __future__ import annotations

import argparse
from pathlib import Path

from database.numeric_migration import UnsafeNumericMigration, inspect, rebuild


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Exact path to ikaros.duckdb")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--write", action="store_true", help="In-place migration where DuckDB dependencies permit"
    )
    action.add_argument(
        "--rebuild",
        action="store_true",
        help="Safely rebuild into a separate database, preserving keys",
    )
    parser.add_argument(
        "--output", type=Path, help="Required output filename for --rebuild (NEW file)"
    )
    parser.add_argument("--report", type=Path, help="Optional JSON audit output path")
    args = parser.parse_args(argv)
    if args.rebuild and args.output is None:
        parser.error("--rebuild requires --output NEW.duckdb")
    if args.output is not None and not args.rebuild:
        parser.error("--output is only valid with --rebuild")
    try:
        if args.rebuild:
            return rebuild(args.database, output=args.output, report=args.report)
        return inspect(args.database, write=args.write, report=args.report)
    except (OSError, ValueError, UnsafeNumericMigration) as exc:
        print(f"NUMERIC STORAGE: {exc}")
        return 2
    except Exception as exc:
        print(f"NUMERIC STORAGE INCOMPLETE ({type(exc).__name__}): {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
