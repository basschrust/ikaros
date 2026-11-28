"""Load independently reviewed WIG20 membership and derivative contract metadata.

The optional CSVs must be sourced and reviewed; no price bars are fabricated.
Default is validation/dry-run. --write adds reference data to LOCAL DuckDB.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from database.daedalus_reference import ensure_reference_schema

WEIGHT_FIELDS = {
    "as_of",
    "index_symbol",
    "ticker",
    "security_symbol",
    "weight_pct",
    "source",
    "source_url",
}
CONTRACT_FIELDS = {
    "contract_id",
    "symbol",
    "venue",
    "instrument_type",
    "underlying_symbol",
    "expiry_date",
    "currency",
    "multiplier",
    "strike",
    "option_side",
    "source",
}


def read_csv(*, path: Path, fields: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not fields.issubset(
            {x.strip().lower() for x in reader.fieldnames}
        ):
            raise ValueError(
                f"{path}: missing required CSV fields: {sorted(fields - set(x.strip().lower() for x in (reader.fieldnames or [])))}"
            )
        rows = [
            {str(k).strip().lower(): str(v or "").strip() for k, v in row.items()} for row in reader
        ]
    if not rows:
        raise ValueError(f"{path}: empty CSV")
    return rows


def positive_decimal(*, value: str, label: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{label} must be strictly positive: {value!r}")
    return number


def validate_weights(*, rows: list[dict[str, str]]) -> tuple[date, list[dict[str, Any]]]:
    if len(rows) != 20:
        raise ValueError(f"Expected exactly 20 WIG20 constituents, got {len(rows)}")
    dates = {date.fromisoformat(row["as_of"]) for row in rows}
    if len(dates) != 1:
        raise ValueError("WIG20 CSV must contain exactly one effective date")
    result = []
    seen_symbols: set[str] = set()
    seen_tickers: set[str] = set()
    for row in rows:
        if row["index_symbol"] != "WIG20":
            raise ValueError("Only WIG20 is supported")
        symbol = row["security_symbol"].upper()
        ticker = row["ticker"].upper()
        if not symbol.endswith(".WA") or not ticker:
            raise ValueError(f"Invalid ticker/security_symbol: {ticker!r}/{symbol!r}")
        if symbol in seen_symbols or ticker in seen_tickers:
            raise ValueError("Duplicate WIG20 symbol or ticker")
        seen_symbols.add(symbol)
        seen_tickers.add(ticker)
        if not row["source"].strip() or not row["source_url"].startswith("https://"):
            raise ValueError("Official snapshot needs source and source_url")
        value = positive_decimal(value=row["weight_pct"], label="weight_pct")
        if value > 100:
            raise ValueError("Weight percentage exceeds 100")
        result.append(
            {
                "symbol": symbol,
                "weight": value / 100,
                "source": f"{row['source']} | {row['source_url']}",
            }
        )
    total = sum((item["weight"] for item in result), Decimal(0))
    if abs(total - Decimal(1)) > Decimal("0.002"):
        raise ValueError(f"WIG20 official weight total {total} is not approximately 1")
    if len({item["source"] for item in result}) != 1:
        raise ValueError("One snapshot must have one common source")
    return next(iter(dates)), result


def validate_contracts(*, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        cid = item["contract_id"].upper()
        if not cid or cid in seen:
            raise ValueError(f"Missing or duplicate contract ID: {cid!r}")
        seen.add(cid)
        kind = item["instrument_type"].upper()
        if kind not in {"FUTURE", "OPTION"}:
            raise ValueError(f"Invalid contract type {kind}")
        if item["symbol"].upper() != cid:
            raise ValueError(f"Contract {cid}: symbol must equal the verified contract ID")
        if item["underlying_symbol"].upper() != "WIG20":
            raise ValueError(f"Contract {cid}: only WIG20 supported")
        # This importer is specifically for GPW WIG20 contracts. Do not accept
        # an unknown venue or silently infer it from the symbol or underlying.
        venue = item.get("venue", "").upper().strip()
        if venue != "GPW":
            raise ValueError(f"Contract {cid}: reviewed WIG20 venue must be GPW, got {venue!r}")
        expiry = date.fromisoformat(item["expiry_date"])
        currency = item["currency"].upper()
        if currency != "PLN":
            raise ValueError(f"Contract {cid}: expected PLN currency")
        multiplier = positive_decimal(value=item["multiplier"], label="multiplier")
        if kind == "FUTURE" and multiplier != 20:
            raise ValueError(f"WIG20 future {cid}: expected 20 PLN/point")
        if kind == "OPTION" and multiplier != 10:
            raise ValueError(f"WIG20 option {cid}: expected 10 PLN/point")
        strike = positive_decimal(value=item["strike"], label="strike") if item["strike"] else None
        side = item["option_side"].upper() or None
        if kind == "OPTION" and (strike is None or side not in {"CALL", "PUT"}):
            raise ValueError(f"Option {cid} requires verified strike and CALL/PUT")
        if kind == "FUTURE" and (strike is not None or side is not None):
            raise ValueError(f"Future {cid} must not have strike or option side")
        if not item["source"].startswith("https://"):
            raise ValueError(f"Contract {cid} requires a verifiable source URL")
        result.append(
            {
                "contract_id": cid,
                "symbol": cid,
                "venue": venue,
                "instrument_type": kind,
                "underlying_symbol": "WIG20",
                "expiry_date": expiry,
                "currency": currency,
                "multiplier": multiplier,
                "strike": strike,
                "option_side": side,
                "source": item["source"],
            }
        )
    return result


def _db_rows(connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return list(connection.execute(query, params).fetchall())


def store_weights(*, connection: Any, as_of: date, items: list[dict[str, Any]]) -> int:
    missing = sorted(
        {x["symbol"] for x in items}
        - {
            str(row[0]).upper()
            for row in _db_rows(
                connection, 'SELECT "SYMBOL" FROM "SECURITIES" WHERE "VENUE"=?', ("GPW",)
            )
        }
    )
    if missing:
        raise ValueError(
            f"Unregistered GPW securities: {missing}; review symbol mapping, seed SECURITIES first"
        )
    existing = _db_rows(
        connection,
        'SELECT "SECURITY_SYMBOL", "WEIGHT", "SOURCE" FROM "INDEX_CONSTITUENTS" '
        'WHERE "INDEX_SYMBOL"=? AND "VALID_FROM"=?',
        ("WIG20", as_of),
    )
    if existing:
        actual = {(str(s), Decimal(str(w)), str(src)) for s, w, src in existing}
        wanted = {(x["symbol"], x["weight"], x["source"]) for x in items}
        if actual != wanted:
            raise ValueError(f"Conflicting existing WIG20 snapshot at {as_of}; not overwriting")
        return 0
    upcoming = _db_rows(
        connection,
        'SELECT MIN("VALID_FROM") FROM "INDEX_CONSTITUENTS" WHERE "INDEX_SYMBOL"=? AND "VALID_FROM">?',
        ("WIG20", as_of),
    )[0][0]
    end = upcoming - timedelta(days=1) if upcoming else None
    connection.execute(
        'UPDATE "INDEX_CONSTITUENTS" SET "VALID_TO"=? '
        'WHERE "INDEX_SYMBOL"=? AND "VALID_FROM"<? '
        'AND ("VALID_TO" IS NULL OR "VALID_TO">=?)',
        (as_of - timedelta(days=1), "WIG20", as_of, as_of),
    )
    for item in items:
        connection.execute(
            'INSERT INTO "INDEX_CONSTITUENTS" ("INDEX_SYMBOL","SECURITY_SYMBOL","WEIGHT","VALID_FROM","VALID_TO","SOURCE") VALUES (?,?,?,?,?,?)',
            ("WIG20", item["symbol"], item["weight"], as_of, end, item["source"]),
        )
    return len(items)


def store_contracts(*, connection: Any, items: list[dict[str, Any]]) -> int:
    fields = (
        "SYMBOL",
        "VENUE",
        "INSTRUMENT_TYPE",
        "UNDERLYING_SYMBOL",
        "EXPIRY_DATE",
        "CURRENCY",
        "MULTIPLIER",
        "STRIKE",
        "OPTION_SIDE",
        "SOURCE",
    )
    new = 0
    for item in items:
        cid = item["contract_id"]
        values = [item[field.lower()] for field in fields]
        current = _db_rows(
            connection,
            "SELECT "
            + ",".join(f'"{name}"' for name in fields)
            + ' FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=?',
            (cid,),
        )
        if len(current) > 1:
            raise ValueError(f"Duplicate contract {cid} already stored")
        if not current:
            connection.execute(
                'INSERT INTO "DERIVATIVE_CONTRACTS" ("CONTRACT_ID",'
                + ",".join(f'"{name}"' for name in fields)
                + ") VALUES ("
                + ",".join("?" for _ in range(len(fields) + 1))
                + ")",
                (cid, *values),
            )
            new += 1
            continue
        to_fill: dict[str, Any] = {}
        for name, old, target in zip(fields, current[0], values):
            if old is None or old == "":
                if target is not None:
                    to_fill[name] = target
                continue
            if target is None:
                # Existing reviewed metadata is more complete than this CSV.
                continue
            if isinstance(target, Decimal):
                same = Decimal(str(old)) == target
            elif isinstance(target, date):
                same = str(old)[:10] == target.isoformat()
            elif name == "SOURCE":
                # The old attribution can remain when adding a second citation.
                same = True
            else:
                same = str(old).upper() == str(target).upper()
            if not same:
                raise ValueError(f"Conflicting {cid}.{name}: existing={old!r}, reviewed={target!r}")
        if to_fill:
            statement = ", ".join(f'"{name}"=?' for name in to_fill)
            connection.execute(
                f'UPDATE "DERIVATIVE_CONTRACTS" SET {statement} WHERE "CONTRACT_ID"=?',
                (*to_fill.values(), cid),
            )
            new += 1
    return new


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights-csv", type=Path)
    parser.add_argument("--contracts-csv", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if not args.weights_csv and not args.contracts_csv:
        parser.error("Supply --weights-csv and/or --contracts-csv")
    if args.weights_csv:
        date_effective, weights = validate_weights(
            rows=read_csv(path=args.weights_csv, fields=WEIGHT_FIELDS)
        )
        print(f"Validated WIG20 snapshot {date_effective}: {len(weights)} constituents")
    if args.contracts_csv:
        contracts = validate_contracts(
            rows=read_csv(path=args.contracts_csv, fields=CONTRACT_FIELDS)
        )
        print(f"Validated contract metadata: {len(contracts)}")
    if not args.write:
        print("DRY RUN: no database touched")
        return 0
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "duckdb":
        parser.error("This command requires local DuckDB: IKAROS_DB_DRIVER=duckdb")
    with repository_from_config(config) as repo:
        connection = repo.connection
        ensure_reference_schema(connection)
        try:
            connection.execute("BEGIN")
            if args.weights_csv:
                print(
                    f"WIG20 snapshot rows inserted: {store_weights(connection=connection, as_of=date_effective, items=weights)}"
                )
            if args.contracts_csv:
                print(
                    f"Derivative contracts added/enriched: {store_contracts(connection=connection, items=contracts)}"
                )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        if args.contracts_csv:
            from database.market_quote_seed import seed_mappings

            # This creates the required BIZNESRADAR aliases for the newly
            # registered exact contracts. It manages its own transaction.
            mapped = seed_mappings(connection, backend="duckdb", write=True)
            print(f"New provider aliases: {mapped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
