"""Repair NULL venues of *verified* GPW WIG20 contracts; no market-data downloads.

Uses retained exact GPW listing evidence in DERIVATIVE_CONTRACTS.SOURCE and,
when available, output/wig20_history/discovered_contracts.csv.

    python -m run.reconcile_gpw_venues            # dry-run
    python -m run.reconcile_gpw_venues --write    # apply metadata-only repair
"""

from __future__ import annotations

import argparse
from pathlib import Path

from database.derivative_venue_reconciliation import (
    apply_gpw_venue_repair,
    plan_gpw_venue_repair,
    read_discovery,
)


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Archived exact GPW discovery CSV")
    parser.add_argument("--write", action="store_true", help="Apply verified metadata-only changes")
    args = parser.parse_args(argv)

    from config.config import PROJECT_ROOT, load_config
    from database.repositories.factory import repository_from_config

    manifest = args.manifest or (
        PROJECT_ROOT / "output" / "wig20_history" / "discovered_contracts.csv"
    )
    try:
        if args.manifest and not manifest.is_file():
            raise ValueError(f"Requested discovery manifest not found: {manifest}")
        if not manifest.is_file():
            print("No discovery manifest: using stored contract source evidence only")
        discovered = read_discovery(path=manifest)
        config = load_config("database.yaml")
        if config.get("database", {}).get("driver", "duckdb").lower() != "duckdb":
            raise ValueError("Venue reconciliation requires the local DuckDB source")
        with repository_from_config(config) as repo:
            connection = repo.connection
            plan = plan_gpw_venue_repair(connection=connection, discovered=discovered)
            print(
                f"GPW venue evidence: eligible={len(plan.eligible)}, "
                f"already_gpw={len(plan.already_gpw)}, "
                f"unknown={len(plan.unresolved)}, other_venue={len(plan.foreign)}"
            )
            if plan.unresolved:
                print("UNVERIFIED VENUE (left unchanged): " + ", ".join(plan.unresolved))
            if plan.eligible:
                print("VERIFIED GPW (source evidence preserved): " + ", ".join(plan.eligible))
            if args.write:
                changed = apply_gpw_venue_repair(connection=connection, plan=plan)
                print(f"UPDATED DERIVATIVE_CONTRACTS.VENUE: {changed} row(s)")
            else:
                print("DRY RUN: database unchanged; add --write to repair")
        return int(bool(plan.unresolved))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"GPW VENUE RECONCILIATION FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
