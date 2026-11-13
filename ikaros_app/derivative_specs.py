"""Verified derivative specification metadata (additive, no synthetic contracts).

Run: python -m ikaros_app.derivative_specs --csv data/verified_specs.csv [--write]
Only existing DERIVATIVE_CONTRACTS can receive specification metadata.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
from typing import Any
from snowflake.connector import SnowflakeConnection

import pandas as pd

FIELDS = (
    "contract_id",
    "product_family",
    "underlying_type",
    "exercise_style",
    "settlement_type",
    "price_unit",
    "tick_size",
    "lot_size",
    "specification_source",
    "source_url",
)
FAMILIES = {"INDEX_FUTURE", "EQUITY_FUTURE", "FX_FUTURE", "INDEX_OPTION", "OTHER"}
UNDERLYINGS = {"INDEX", "EQUITY", "FX_PAIR", "OTHER"}


def _optional(value: Any) -> Any:
    return None if pd.isna(value) or str(value).strip() == "" else value


def validate_manifest(frame: pd.DataFrame) -> list[dict]:
    missing = set(FIELDS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing specification columns: {sorted(missing)}")
    if frame["contract_id"].duplicated().any():
        raise ValueError("Duplicate contract_id in verified specifications")
    result = []
    for rec in frame[list(FIELDS)].to_dict("records"):
        item = {k: _optional(v) for k, v in rec.items()}
        for key in (
            "contract_id",
            "product_family",
            "underlying_type",
            "price_unit",
            "specification_source",
            "source_url",
        ):
            if item[key] is None:
                raise ValueError(f"{key} is required for verification")
            item[key] = str(item[key]).strip()
        if item["product_family"] not in FAMILIES:
            raise ValueError(f"Invalid product_family {item['product_family']}")
        if item["underlying_type"] not in UNDERLYINGS:
            raise ValueError("Unknown underlying_type")
        if (item["product_family"] == "INDEX_OPTION") != (item["exercise_style"] is not None):
            raise ValueError("Options require exercise_style, futures must omit it")
        for key in ("tick_size", "lot_size"):
            if item[key] is None or not (float(item[key]) > 0):
                raise ValueError(f"Positive verified {key} required")
            item[key] = float(item[key])
        if item["settlement_type"] not in ("CASH", "PHYSICAL"):
            raise ValueError("Verified settlement_type must be CASH or PHYSICAL")
        result.append(item)
    return result


def register_metadata(
    connection: SnowflakeConnection, rows: list[dict], *, write: bool
) -> tuple[int, int]:
    """Create missing metadata, refuse conflicts; no changes to contracts/prices.

    Requires sql/derivatives/01_extend_registry.sql previously applied.
    """
    planned = 0
    already = 0
    with closing(connection.cursor()) as cur:
        for item in rows:
            cur.execute(
                'SELECT "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=%s',
                (item["contract_id"],),
            )
            if len(cur.fetchall()) != 1:
                raise ValueError(
                    f"Verified contract not registered exactly once: {item['contract_id']}"
                )
            cur.execute(
                "SELECT "
                + ",".join('"' + f.upper() + '"' for f in FIELDS[1:])
                + ' FROM "DERIVATIVE_SPECIFICATIONS" WHERE "CONTRACT_ID"=%s',
                (item["contract_id"],),
            )
            found = cur.fetchall()
            if len(found) > 1:
                raise ValueError(f"Duplicate specification metadata: {item['contract_id']}")
            if found:
                expected = [item[f] for f in FIELDS[1:]]
                if any(str(a) != str(b) for a, b in zip(found[0], expected)):
                    raise ValueError(f"Specification conflict: {item['contract_id']}")
                already += 1
                continue
            planned += 1
            if write:
                cur.execute(
                    'INSERT INTO "DERIVATIVE_SPECIFICATIONS" ('
                    + ",".join('"' + f.upper() + '"' for f in FIELDS)
                    + ',"VERIFIED_AT") VALUES ('
                    + ",".join(["%s"] * len(FIELDS))
                    + ",CURRENT_TIMESTAMP())",
                    tuple(item[f] for f in FIELDS),
                )
    return planned, already


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    rows = validate_manifest(pd.read_csv(args.csv, dtype={"contract_id": "string"}))
    print(f"Validated {len(rows)} verified specifications; write={args.write}")
    # Dry run also checks database references and duplicates, but never modifies it.
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    with repository_from_config(load_config("database.yaml")) as repo:
        planned, already = register_metadata(repo.connection, rows, write=args.write)
    print(f"new={planned} existing={already} write={args.write}")


if __name__ == "__main__":
    main()
