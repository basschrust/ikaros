"""Controlled registration of manually verified Ikaros derivative candidates.

Run from project root:
  uv run --extra snowflake python -m ikaros_app.derivative_approval --csv output/derivative_approval_review.csv
  uv run --extra snowflake python -m ikaros_app.derivative_approval --csv output/derivative_approval_review.csv --write

The generated review CSV has approval=NO for every row. An operator must
independently verify exact contract terms and set approval=YES, supply the
verified_* fields and an evidence_url. No implicit approval is performed.
"""

from __future__ import annotations
import argparse
import csv
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from snowflake.connector import SnowflakeConnection

REQUIRED = (
    "symbol",
    "classification",
    "verified_underlying_symbol",
    "verified_expiry_date",
    "verified_multiplier",
    "currency",
    "source",
    "evidence_url",
    "approval",
)
FIELDS = (
    "symbol",
    "classification",
    "verified_underlying_symbol",
    "verified_expiry_date",
    "currency",
    "verified_multiplier",
    "verified_strike",
    "verified_option_side",
    "source",
)


def parse_approved(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rdr = csv.DictReader(handle)
        if not set(REQUIRED) <= set(rdr.fieldnames or ()):
            raise ValueError("Missing approval columns")
        raw = list(rdr)
    seen = set()
    approved = []
    for row in raw:
        symbol = row["symbol"].strip().upper()
        if symbol in seen:
            raise ValueError(f"Duplicate symbol: {symbol}")
        seen.add(symbol)
        if row["approval"].strip().upper() != "YES":
            continue
        for field in REQUIRED:
            if not row.get(field, "").strip():
                raise ValueError(f"{symbol}: missing {field}")
        if row["classification"] not in ("FUTURE", "OPTION"):
            raise ValueError(f"{symbol}: invalid classification")
        expiry = date.fromisoformat(row["verified_expiry_date"])
        multiplier = float(row["verified_multiplier"])
        if multiplier <= 0:
            raise ValueError(f"{symbol}: invalid multiplier")
        parsed = urlparse(row["evidence_url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{symbol}: evidence_url must be an HTTPS source")
        option = row["classification"] == "OPTION"
        side = row.get("verified_option_side", "").upper().strip()
        strike_raw = row.get("verified_strike", "").strip()
        if option and (side not in {"CALL", "PUT"} or not strike_raw):
            raise ValueError(f"{symbol}: option side and strike must be verified")
        if not option and (side or strike_raw):
            raise ValueError(f"{symbol}: futures cannot have option fields")
        strike = float(strike_raw) if option else None
        if option and strike <= 0:
            raise ValueError(f"{symbol}: invalid strike")
        # In this review, the source dates universally precede standard expiry.
        # A verified date must therefore have a separately cited source.
        approved.append(
            dict(
                contract_id=symbol,
                symbol=symbol,
                instrument_type=row["classification"],
                underlying_symbol=row["verified_underlying_symbol"],
                expiry_date=expiry,
                currency=row["currency"],
                multiplier=multiplier,
                strike=strike,
                option_side=side if option else None,
                source="verified:" + parsed.netloc,
            )
        )
    return approved


def promote(connection: SnowflakeConnection, contracts: list[dict], *, write: bool) -> None:
    """Preflight *all* candidates before writing any one contract.

    A failed existing-contract comparison should not leave partially registered
    contracts behind. For live writes, use only one writer (Snowflake standard
    table constraints do not enforce uniqueness).
    """
    planned = []
    for contract in contracts:
        symbol = contract["symbol"]
        with connection.cursor() as cur:
            cur.execute(
                'SELECT "SYMBOL", "INSTRUMENT_TYPE", "UNDERLYING_SYMBOL", '
                '"EXPIRY_DATE", "CURRENCY", "MULTIPLIER", "STRIKE", "OPTION_SIDE" '
                'FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=%s',
                (symbol,),
            )
            existing = cur.fetchall()
            if len(existing) > 1:
                raise ValueError(f"{symbol}: duplicate registered contracts")
            cur.execute(
                'SELECT COUNT(*) FROM "DERIVATIVE_CANDIDATES" WHERE "SYMBOL"=%s',
                (symbol,),
            )
            if not cur.fetchone()[0]:
                raise ValueError(f"{symbol}: candidate not present in Snowflake")
            expected = (
                symbol,
                contract["instrument_type"],
                contract["underlying_symbol"],
                contract["expiry_date"],
                contract["currency"],
                contract["multiplier"],
                contract["strike"],
                contract["option_side"],
            )
            if existing:
                actual = list(existing[0])
                if isinstance(actual[3], datetime):
                    actual[3] = actual[3].date()
                if (
                    tuple(actual[:5]) != expected[:5]
                    or float(actual[5]) != expected[5]
                    or (None if actual[6] is None else float(actual[6])) != expected[6]
                    or actual[7] != expected[7]
                ):
                    raise ValueError(f"{symbol}: CONFLICT with existing contract; not updating")
            planned.append((contract, expected, bool(existing)))

    existing_count = sum(item[2] for item in planned)
    print(
        f"PREFLIGHT OK: {len(planned)} candidates, "
        f"new={len(planned) - existing_count}, existing={existing_count}, write={write}"
    )
    if not write:
        return
    # One DML transaction for all registration and status changes.
    # Avoid DDL in this transaction because Snowflake DDL auto-commits.
    with connection.cursor() as cur:
        try:
            cur.execute("BEGIN")
            for contract, expected, exists in planned:
                if not exists:
                    cur.execute(
                        'INSERT INTO "DERIVATIVE_CONTRACTS" '
                        '("CONTRACT_ID","SYMBOL","INSTRUMENT_TYPE","UNDERLYING_SYMBOL",'
                        '"EXPIRY_DATE","CURRENCY","MULTIPLIER","STRIKE","OPTION_SIDE","SOURCE") '
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (contract["contract_id"], *expected, contract["source"]),
                    )
                cur.execute(
                    'UPDATE "DERIVATIVE_CANDIDATES" SET "REVIEW_STATUS"=%s WHERE "SYMBOL"=%s',
                    ("REGISTERED", contract["symbol"]),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    print(f"REGISTERED/CONFIRMED {len(planned)} contracts")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    approved = parse_approved(args.csv)
    print(f"Explicitly approved contracts: {len(approved)}; write={args.write}")
    if not approved:
        print("No approvals. Review exact expiry, multipliers and evidence URLs first.")
        return
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Snowflake driver required")
    with repository_from_config(config) as repo:
        promote(repo.connection, approved, write=args.write)


if __name__ == "__main__":
    main()
