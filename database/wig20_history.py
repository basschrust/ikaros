"""Local WIG20 history planning and coverage; no authoritative price writes."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from run.backfill_local import BackfillJob


def gpw_calendar(*, root: Path) -> tuple[set[date], set[int]]:
    config = yaml.safe_load((root / "config" / "calendars.yaml").read_text(encoding="utf-8"))
    calendar = next(item for item in config["calendars"] if item["name"] == "calendar_GPW")
    closures = {date.fromisoformat(str(value)) for value in calendar["non_trading_days"]}
    return closures, {value.year for value in closures}


def sessions(*, start: date, end: date, closures: set[date], years: set[int]) -> set[date]:
    if start > end:
        raise ValueError("Invalid history range")
    missing_years = set(range(start.year, end.year + 1)) - years
    if missing_years:
        raise ValueError(f"GPW calendar not maintained for {sorted(missing_years)}")
    return {
        start + timedelta(days=n)
        for n in range((end - start).days + 1)
        if (start + timedelta(days=n)).weekday() < 5 and start + timedelta(days=n) not in closures
    }


def equity_jobs(
    *, connection: Any, symbols: tuple[str, ...], required: set[date]
) -> list[BackfillJob]:
    from database.daedalus_readiness import price_dates
    from run.backfill_local import BackfillJob, date_windows
    from run.prepare_daedalus import missing_ranges

    target = max(required)
    jobs = []
    for dataset, instruments in (("WIG20", ("WIG20",)), ("GPW", symbols)):
        grouped: dict[tuple[date, date], list[str]] = {}
        for symbol in instruments:
            existing = price_dates(
                connection=connection, dataset=dataset, symbol=symbol, target=target
            )
            for begin, end in missing_ranges(missing=required - existing):
                for first, last in date_windows(start=begin, end=end, days=60):
                    grouped.setdefault((first, last), []).append(symbol)
        for (first, last), batch in sorted(grouped.items()):
            jobs.append(BackfillJob(dataset, first, last, tuple(batch)))
    return jobs


def derivative_rejections(
    *, connection: Any, symbol: str, start: date, end: date
) -> dict[str, int]:
    rows = connection.execute(
        'SELECT "VALIDATION_REASON", COUNT(*) FROM "DERIVATIVE_PRICE_CANDIDATES" '
        'WHERE "CONTRACT_ID"=? AND "DATE" BETWEEN ? AND ? '
        'AND "VALIDATION_STATUS"<>\'VALID\' GROUP BY "VALIDATION_REASON"',
        (symbol, start, end),
    ).fetchall()
    return {str(reason): int(count) for reason, count in rows}


def extend_archive_mappings(*, connection: Any, root: Path, start: date) -> int:
    """Use reviewed equity archive aliases before the starter snapshot date.

    Never rewrite a provider mapping ID: candidate rows can reference it. The
    dated MDV and EBP exchange ticker transitions are not backdated. Existing
    Yahoo mappings remain unchanged. Provider histories may still have gaps.
    """
    from database.provider_symbols_seed import (
        SymbolMapping,
        plan_seed,
        read_existing_mappings,
        read_existing_providers,
        read_reviewed_csv,
        read_securities,
    )

    with connection.cursor() as cur:
        securities = read_securities(cur, venue="GPW")
        reviewed = read_reviewed_csv(
            root / "data" / "provider_symbols_gpw_wig20.csv", securities, "GPW"
        )
        providers = read_existing_providers(cur)
        existing = read_existing_mappings(cur)
        earliest = {}
        for mapping in reviewed:
            if mapping.provider_code not in {"BIZNESRADAR", "STOOQ"}:
                continue
            key = (mapping.asset_id, mapping.provider_code)
            if key not in earliest or mapping.valid_from < earliest[key].valid_from:
                earliest[key] = mapping
        symbols = {security.asset_id: security.symbol for security in securities.values()}
        requests = []
        for mapping in earliest.values():
            lower = max(start, date(2025, 1, 1))
            symbol = symbols[mapping.asset_id]
            # Keep exchange ticker changes explicit. Yahoo's existing archive
            # mapping may cover pre-rename data; the coverage report detects gaps.
            if symbol == "MDV.WA":
                lower = max(lower, date(2026, 2, 19))
            elif symbol == "EBP.WA":
                lower = max(lower, date(2026, 4, 28))
            upper = mapping.valid_from - timedelta(days=1)
            if lower > upper:
                continue
            # Subtract existing intervals, including user-reviewed older aliases.
            gaps = [(lower, upper)]
            for old in existing:
                if (
                    old.asset_id != mapping.asset_id
                    or old.provider_id != providers[mapping.provider_code]
                ):
                    continue
                remaining = []
                old_end = old.valid_to or date.max
                for left, right in gaps:
                    if old.valid_from > right or old_end < left:
                        remaining.append((left, right))
                    else:
                        if left < old.valid_from:
                            remaining.append((left, old.valid_from - timedelta(days=1)))
                        if old_end < right:
                            remaining.append((old_end + timedelta(days=1), right))
                gaps = remaining
            requests.extend(
                SymbolMapping(
                    mapping.asset_id, mapping.provider_code, mapping.provider_symbol, left, right
                )
                for left, right in gaps
            )
        plan = plan_seed(providers, existing, requests)
        if plan.providers:
            raise ValueError("Run starter provider seeding before extending archive mappings")
        cur.execute("BEGIN")
        try:
            for identifier, mapping in plan.mappings:
                cur.execute(
                    'INSERT INTO "PROVIDER_SYMBOLS" ("PROVIDER_SYMBOL_ID", "ASSET_ID", '
                    '"PROVIDER_ID", "PROVIDER_SYMBOL", "VALID_FROM", "VALID_TO") VALUES (?,?,?,?,?,?)',
                    (
                        identifier,
                        mapping.asset_id,
                        providers[mapping.provider_code],
                        mapping.provider_symbol,
                        mapping.valid_from,
                        mapping.valid_to,
                    ),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return len(plan.mappings)
