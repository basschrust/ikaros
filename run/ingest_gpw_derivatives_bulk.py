"""Bulk derivative import: canonical contract manifest and per-contract OHLCV CSVs.

Dry run is fully offline. --write uses existing Snowflake contract/prices MERGE.
Prices must be files named <contract_id>.csv within --prices-dir.
No discovery of live exchange listings or fetching external websites occurs here.
"""
from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

import pandas as pd

from ingestion.derivatives import PRICE_COLUMNS, validate_contracts, validate_prices

logger = logging.getLogger(__name__)


def price_path(prices_dir: Path, contract_id: str) -> Path:
    """Disallow traversal or unusual filename components from input manifests."""
    if not contract_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in contract_id):
        raise ValueError(f"Unsafe contract_id for filename: {contract_id!r}")
    return prices_dir / f"{contract_id}.csv"


def collect(
    contracts: pd.DataFrame, prices_dir: Path, *, allow_missing: bool = False
) -> tuple[pd.DataFrame, list[dict[str, str | int]]]:
    frames: list[pd.DataFrame] = []
    report: list[dict[str, str | int]] = []
    ids = set(contracts["contract_id"])
    for rec in contracts.itertuples(index=False):
        path = price_path(prices_dir, rec.contract_id)
        if not path.is_file():
            report.append({"contract_id": rec.contract_id, "status": "MISSING_FILE", "rows": 0, "detail": str(path)})
            continue
        try:
            raw = pd.read_csv(path, dtype={"contract_id": "string"})
            if "contract_id" not in raw:
                raise ValueError("Missing contract_id column")
            if set(raw["contract_id"].dropna()) != {rec.contract_id}:
                raise ValueError("File must contain only its named contract_id")
            frame = validate_prices(raw, ids)
            if frame.empty:
                report.append({"contract_id": rec.contract_id, "status": "NO_DATA", "rows": 0, "detail": "Empty CSV"})
            else:
                frames.append(frame)
                report.append({"contract_id": rec.contract_id, "status": "READY", "rows": len(frame), "detail": ""})
        except Exception as exc:
            report.append({"contract_id": rec.contract_id, "status": "INVALID", "rows": 0, "detail": str(exc)})
    problems = [entry for entry in report if entry["status"] != "READY"]
    if problems and not allow_missing:
        raise ValueError("Bulk import incomplete: " + "; ".join(f'{r["contract_id"]}:{r["status"]} {r["detail"]}' for r in problems[:12]))
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)
    return result, report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--contracts", type=Path, required=True)
    p.add_argument("--prices-dir", type=Path, required=True)
    p.add_argument("--report", type=Path, default=Path("output/derivative_bulk_report.csv"))
    p.add_argument("--allow-missing", action="store_true", help="Proceed with valid contracts; report missing/invalid files")
    p.add_argument("--write", action="store_true", help="Insert/refresh data in Snowflake")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    contracts = validate_contracts(pd.read_csv(args.contracts, dtype={"contract_id": "string"}))
    observations, report = collect(contracts, args.prices_dir, allow_missing=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["contract_id", "status", "rows", "detail"])
        writer.writeheader()
        writer.writerows(report)
    for row in report:
        logger.info("%s: %s rows=%s %s", row["contract_id"], row["status"], row["rows"], row["detail"])
    errors = [r for r in report if r["status"] != "READY"]
    logger.info("Total: contracts=%d, valid_observations=%d, issues=%d, report=%s", len(contracts), len(observations), len(errors), args.report)
    if errors and not args.allow_missing:
        p.error("Some contracts have missing or invalid prices. Use --allow-missing to import valid contracts only.")
    if not args.write:
        logger.info("DRY RUN complete: Snowflake untouched")
        return
    if observations.empty:
        p.error("No valid prices available to write")
    # Keep reference writes limited to contracts that have validated price data.
    selected = contracts[contracts["contract_id"].isin(set(observations["contract_id"]))]
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from run.ingest_gpw_derivatives import DDL, check_references, persist_prices

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("config/database.yaml must select Snowflake")
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            for statement in DDL:
                cur.execute(statement)
        check_references(repo.connection, selected, write=True)
        persist_prices(repo.connection, observations)
    logger.info("Imported %d contracts with %d valid price rows", len(selected), len(observations))


if __name__ == "__main__":
    main()
