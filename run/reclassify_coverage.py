"""Upgrade an existing WIG20 coverage.csv without repeating provider downloads.

Read the current Ikaros reference database, not ticker suffixes or old DATASET
labels, to determine VENUE and INSTRUMENT_TYPE.

    python -m run.reclassify_coverage
    python -m run.reclassify_coverage --write

Writes coverage_venue.csv alongside the original; never overwrites coverage.csv.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from database.coverage_identity import COVERAGE_FIELDS, reclassify_existing_coverage


def load_legacy(*, path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Coverage CSV not found: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Coverage CSV is missing its header")
        headers = [field.strip().lower() for field in reader.fieldnames]
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate coverage CSV columns")
        if "dataset" not in headers:
            raise ValueError("Expected old coverage CSV with DATASET column; already converted?")
        if "symbol" not in headers:
            raise ValueError("Coverage CSV is missing SYMBOL")
        rows = [
            {str(name).strip().lower(): str(value or "") for name, value in record.items()}
            for record in reader
        ]
    if not rows:
        raise ValueError("Coverage CSV has no rows")
    return rows


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write", action="store_true", help="Write a new CSV; keep original")
    args = parser.parse_args(argv)

    from config.config import PROJECT_ROOT, load_config
    from database.repositories.factory import repository_from_config

    source = args.source or PROJECT_ROOT / "output" / "wig20_history" / "coverage.csv"
    output = args.output or source.with_name("coverage_venue.csv")
    if source.resolve() == output.resolve():
        parser.error("--output cannot overwrite the original coverage.csv")
    try:
        legacy = load_legacy(path=source)
        with repository_from_config(load_config("database.yaml")) as repo:
            normalized, missing = reclassify_existing_coverage(
                connection=repo.connection, rows=legacy
            )
        # Do not silently drop later custom fields added to older reports.
        extra = [key for key in legacy[0] if key not in COVERAGE_FIELDS and key != "dataset"]
        fields = [*COVERAGE_FIELDS, *extra]
        print(
            f"Rows={len(normalized)}, venues={sorted({r['venue'] for r in normalized})}, "
            f"instrument types={sorted({r['instrument_type'] for r in normalized})}"
        )
        if missing:
            print(f"MISSING VERIFIED VENUE for {len(missing)} contracts: {', '.join(missing)}")
        if not args.write:
            print("DRY RUN: add --write to create coverage_venue.csv")
            return int(bool(missing))
        if output.exists():
            raise ValueError(f"Output already exists; refusing overwrite: {output}")
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(normalized)
        print(f"Wrote {output} (original preserved)")
        return int(bool(missing))
    except (OSError, ValueError) as exc:
        print(f"COVERAGE RECLASSIFICATION FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
