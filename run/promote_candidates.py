"""Independent reconciler: read committed candidates, select, publish.

Never calls data providers, web APIs or data-collection modules.
    uv run --extra snowflake python -m run.promote_candidates --dataset WIG20 --start 2026-11-16 --end 2026-11-18 --write
"""

from __future__ import annotations
import argparse
from datetime import date
from run.stage_candidates import DATASETS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, choices=DATASETS)
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument(
        "--write", action="store_true", help="Publish selected candidates to final tables"
    )
    a = p.parse_args(argv)
    if a.start > a.end:
        p.error("start after end")
    if not a.write:
        print("DRY RUN: no promotions; add --write to reconcile persisted candidates")
        return 0
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from database.candidate_promotion import promote_dataset

    cfg = load_config("database.yaml")
    backend = cfg.get("database", {}).get("driver", "").lower()
    if backend not in ("snowflake", "duckdb"):
        p.error("Select Snowflake or DuckDB backend")
    with repository_from_config(cfg) as repo:
        report = promote_dataset(
            repo.connection,
            dataset=a.dataset,
            backend=backend,
            start=a.start,
            end=a.end,
            symbols=a.symbols,
        )
    print(
        f"PROMOTED {a.dataset}: considered={report.considered} "
        f"inserted={report.promoted} existing={report.identical} "
        f"conflicts={report.conflicts} invalid={report.invalid}"
    )
    return 1 if report.conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
