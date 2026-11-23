"""Import 27 sourced PZU 2026 EOD candles into local Ikaros, candidate first.

Dry-run by default. Only PZU.WA is accepted; no arbitrary security price writes.
The importer validates an externally reviewed static CSV and stages/publishes
it through the existing GPW candidate pipeline, never bypassing its safeguards.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SOURCE = "https://www.biznesradar.pl/notowania-historyczne/PZU"


def parse_history(*, path: Path) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal, int]]:
    """Read strict fixed-point PZU OHLCV history from an attributed CSV."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"symbol", "date", "open", "high", "low", "close", "volume", "source_url"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"Missing CSV fields: {sorted(required - set(reader.fieldnames or []))}"
            )
        records = list(reader)
    if not records:
        raise ValueError("No PZU history rows in CSV")
    rows: list[tuple[date, Decimal, Decimal, Decimal, Decimal, int]] = []
    seen: set[date] = set()
    for item in records:
        if item["symbol"] != "PZU.WA" or item["source_url"] != SOURCE:
            raise ValueError("Only PZU.WA verified BiznesRadar rows are accepted")
        day = date.fromisoformat(item["date"])
        if not date(2026, 10, 14) <= day <= date(2026, 11, 20):
            raise ValueError(f"Unexpected date {day}: outside reviewed PZU gap")
        if day in seen:
            raise ValueError(f"Duplicate PZU date: {day}")
        seen.add(day)
        try:
            op, hi, lo, close = (Decimal(item[field]) for field in ("open", "high", "low", "close"))
            vol = int(item["volume"])
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid PZU OHLCV on {day}") from exc
        if any(not x.is_finite() or x <= 0 for x in (op, hi, lo, close)):
            raise ValueError(f"Nonpositive/nonfinite PZU price {day}")
        if hi < max(op, close) or lo > min(op, close) or vol <= 0:
            raise ValueError(f"Inconsistent PZU daily bar {day}")
        rows.append((day, op, hi, lo, close, vol))
    return sorted(rows)


def get_biznesradar_mapping(*, connection: Any) -> Any:
    """Register reviewed PZU BiznesRadar alias if missing, without changing other mappings."""
    from database.gpw_price_pipeline import Mapping

    security = connection.execute(
        'SELECT "ASSET_ID" FROM "SECURITIES" WHERE "VENUE"=? AND "SYMBOL"=?',
        ("GPW", "PZU.WA"),
    ).fetchall()
    if len(security) != 1:
        raise ValueError("PZU.WA must exist exactly once in SECURITIES")
    asset_id = int(security[0][0])
    provider = connection.execute(
        'SELECT "PROVIDER_ID","PRIORITY" FROM "PROVIDERS" WHERE "PROVIDER_CODE"=? AND "IS_ACTIVE"=TRUE',
        ("BIZNESRADAR",),
    ).fetchall()
    if len(provider) != 1:
        raise ValueError("Active BIZNESRADAR provider must exist (run setup_local_duckdb)")
    provider_id, priority = map(int, provider[0])
    mappings = connection.execute(
        'SELECT "PROVIDER_SYMBOL_ID","PROVIDER_SYMBOL","VALID_FROM","VALID_TO", "IS_ACTIVE" '
        'FROM "PROVIDER_SYMBOLS" WHERE "ASSET_ID"=? AND "PROVIDER_ID"=?',
        (asset_id, provider_id),
    ).fetchall()
    if mappings:
        if len(mappings) != 1 or str(mappings[0][1]).upper() != "PZU" or not mappings[0][4]:
            raise ValueError("Conflicting PZU BiznesRadar provider mapping; manual review required")
        mapping_id = int(mappings[0][0])
        start = mappings[0][2]
        start = start if isinstance(start, date) else date.fromisoformat(str(start)[:10])
        end = mappings[0][3]
        end = end if isinstance(end, date) or end is None else date.fromisoformat(str(end)[:10])
        if start > date(2026, 10, 14) or (end is not None and end < date(2026, 11, 20)):
            raise ValueError("PZU BiznesRadar mapping does not cover the imported date range")
    else:
        mapping_id = (
            int(
                connection.execute(
                    'SELECT COALESCE(MAX("PROVIDER_SYMBOL_ID"),0) FROM "PROVIDER_SYMBOLS"'
                ).fetchone()[0]
            )
            + 1
        )
        start, end = date(1990, 1, 1), None
        connection.execute(
            'INSERT INTO "PROVIDER_SYMBOLS" '
            '("PROVIDER_SYMBOL_ID","ASSET_ID","PROVIDER_ID","PROVIDER_SYMBOL","VALID_FROM","IS_ACTIVE") '
            "VALUES (?,?,?,?,?,TRUE)",
            (mapping_id, asset_id, provider_id, "PZU", start),
        )
    return Mapping(
        asset_id,
        "PZU.WA",
        "BIZNESRADAR",
        provider_id,
        mapping_id,
        "PZU",
        priority,
        start,
        end,
    )


def main(*, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    history = parse_history(path=args.csv)
    print(f"PZU: validated {len(history)} real source candles ({history[0][0]}..{history[-1][0]})")
    if not args.write:
        print("DRY RUN: database untouched")
        return 0

    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from database.gpw_price_pipeline import make_candidate
    from database.candidate_stage_queries import store_equity_candidates
    from database.candidate_promotion import promote_dataset
    from ingestion.gpw_provider_feeds import RawCandle

    settings = load_config("database.yaml")
    if settings.get("database", {}).get("driver", "").lower() != "duckdb":
        parser.error("Requires IKAROS_DB_DRIVER=duckdb")
    with repository_from_config(settings) as repo:
        connection = repo.connection
        # Ensure mapping exists. Candidate writes have their own transaction.
        mapping = get_biznesradar_mapping(connection=connection)
        candidates = [
            make_candidate(mapping, RawCandle(day, op, hi, lo, close, vol), positive_volume=True)
            for day, op, hi, lo, close, vol in history
        ]
        invalid = [x for x in candidates if x.status != "VALID"]
        if invalid:
            raise ValueError(f"PZU candidate validation rejected {len(invalid)} candles")
        new = store_equity_candidates(connection, candidates, backend="duckdb")
        report = promote_dataset(
            connection,
            dataset="GPW",
            backend="duckdb",
            start=history[0][0],
            end=history[-1][0],
            symbols=["PZU.WA"],
        )
        print(
            f"PZU candidates new={new} published={report.promoted} "
            f"already={report.identical} conflicts={report.conflicts} invalid={report.invalid}"
        )
        last = connection.execute(
            'SELECT MAX(CAST("DATE" AS DATE)) FROM "PRICES" WHERE "SYMBOL"=?',
            ("PZU.WA",),
        ).fetchone()[0]
        print(f"PZU authoritative last session: {last}")
        if report.conflicts or report.invalid or last is None or last < date(2026, 11, 20):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
