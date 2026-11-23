"""Add FX/rate candidate tables and seed reviewed Yahoo foreign-equity aliases.

Use once after normal Ikaros schema setup. Additive/idempotent; no price writes.
    uv run --extra snowflake python -m run.setup_candidate_flow --write
"""

from __future__ import annotations
import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    if not a.write:
        print(
            "DRY RUN: add FX_RATE_CANDIDATES, RATE_OBSERVATION_CANDIDATES, "
            "integer sequences, provenance columns and reviewed Yahoo symbol mappings"
        )
        return 0
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from database.candidate_observations import ensure_schema
    from database.candidate_catalog import register_foreign_yahoo_mappings

    settings = load_config("database.yaml")
    backend = settings.get("database", {}).get("driver", "").lower()
    if backend not in ("snowflake", "duckdb"):
        p.error("Select Snowflake or DuckDB")
    with repository_from_config(settings) as repo:
        ensure_schema(repo.connection, backend=backend)
        added = register_foreign_yahoo_mappings(repo.connection, backend=backend, write=True)
    print(f"Candidate flow ready; foreign Yahoo symbol mappings added: {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
