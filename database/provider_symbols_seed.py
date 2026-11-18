"""Seed provider identities and verified symbol mappings from SECURITIES.

The authoritative instrument is SECURITIES.ASSET_ID. Automatic mappings are
limited to the Yahoo-style GPW stock symbols already registered in Ikaros.
Mappings for other sources must be explicitly reviewed and supplied as CSV.
No table DDL is defined here: database.schema remains the sole schema authority.
"""

from __future__ import annotations

import csv
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

EARLIEST_MAPPING_DATE = date(1900, 1, 1)
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    code: str
    name: str
    priority: int
    is_active: bool


# These identify underlying price sources, NOT the OpenBB adapter.
# Alternative sources can be activated explicitly after mappings are reviewed.
PROVIDERS = (
    Provider("YFINANCE", "Yahoo Finance", 10, True),
    Provider("STOOQ", "Stooq", 20, False),
    Provider("BIZNESRADAR", "BiznesRadar", 30, False),
)


@dataclass(frozen=True)
class Security:
    asset_id: int
    symbol: str
    venue: str
    instrument_class: str
    is_active: bool


@dataclass(frozen=True)
class SymbolMapping:
    asset_id: int
    provider_code: str
    provider_symbol: str
    valid_from: date
    valid_to: date | None = None


@dataclass(frozen=True)
class ExistingMapping:
    mapping_id: int
    asset_id: int
    provider_id: int
    provider_symbol: str
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class SeedPlan:
    providers: tuple[tuple[int, Provider], ...]
    mappings: tuple[tuple[int, SymbolMapping], ...]
    existing_count: int
    activate_ids: tuple[int, ...] = ()


def _date_value(value: object, *, nullable: bool = False) -> date | None:
    if value is None or value == "":
        if nullable:
            return None
        raise ValueError("valid_from is required")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date: {value!r}") from exc


def _is_true(value: object) -> bool:
    return value is None or value is True or value == 1 or str(value).upper() == "TRUE"


def read_securities(cursor: Any, *, venue: str) -> dict[str, Security]:
    """Read the existing Ikaros reference table; no separate stock CSV needed."""
    cursor.execute(
        'SELECT "ASSET_ID", "SYMBOL", "VENUE", "INSTRUMENT_CLASS", "IS_ACTIVE" '
        'FROM "SECURITIES" WHERE "VENUE" = %s',
        (venue,),
    )
    by_symbol: dict[str, Security] = {}
    by_id: dict[int, str] = {}
    for asset_id, symbol, row_venue, instrument_class, active in cursor.fetchall():
        if asset_id is None or not symbol:
            raise ValueError("SECURITIES contains an asset without ASSET_ID or SYMBOL")
        identifier = int(asset_id)
        canonical = str(symbol).strip()
        if canonical in by_symbol or (identifier in by_id and by_id[identifier] != canonical):
            raise ValueError(f"Ambiguous SECURITIES mapping: {canonical} / {identifier}")
        by_id[identifier] = canonical
        by_symbol[canonical] = Security(
            identifier, canonical, str(row_venue), str(instrument_class or ""), _is_true(active)
        )
    if not by_symbol:
        raise ValueError(f"No securities registered for venue {venue}; seed SECURITIES first")
    return by_symbol


def read_reviewed_csv(
    path: Path, securities: dict[str, Security], venue: str, *, skip_unregistered: bool = False
) -> list[SymbolMapping]:
    """Parse explicitly reviewed mappings; never infer non-Yahoo symbols."""
    expected = {
        "canonical_symbol",
        "venue",
        "provider_code",
        "provider_symbol",
        "valid_from",
        "valid_to",
    }
    rows: list[SymbolMapping] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or set(reader.fieldnames) != expected:
            raise ValueError(f"Mapping CSV must have exactly columns {sorted(expected)}")
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed mapping CSV at line {line_number}")
            row_venue = row["venue"].strip().upper()
            if row_venue != venue:
                raise ValueError(f"Line {line_number}: unexpected venue {row_venue}")
            canonical = row["canonical_symbol"].strip()
            security = securities.get(canonical)
            if security is None:
                if skip_unregistered:
                    LOG.warning(
                        "SKIP UNREGISTERED GPW security %s at CSV line %s", canonical, line_number
                    )
                    continue
                raise ValueError(f"Line {line_number}: unknown/inactive security {canonical}")
            if not security.is_active:
                raise ValueError(f"Line {line_number}: unknown/inactive security {canonical}")
            if security.instrument_class.lower() not in {"stock", "equity"}:
                raise ValueError(f"Line {line_number}: {canonical} is not an equity")
            provider_code = row["provider_code"].strip().upper()
            if provider_code not in {provider.code for provider in PROVIDERS}:
                raise ValueError(f"Line {line_number}: unknown provider {provider_code}")
            external_symbol = row["provider_symbol"].strip()
            if not external_symbol or len(external_symbol) > 128:
                raise ValueError(f"Line {line_number}: invalid provider symbol")
            start = _date_value(row["valid_from"].strip())
            end = _date_value(row["valid_to"].strip(), nullable=True)
            assert start is not None
            if end is not None and end < start:
                raise ValueError(f"Line {line_number}: valid_to precedes valid_from")
            rows.append(
                SymbolMapping(security.asset_id, provider_code, external_symbol, start, end)
            )
    return rows


def wanted_mappings(
    securities: dict[str, Security], *, venue: str, reviewed: list[SymbolMapping]
) -> list[SymbolMapping]:
    """Generate Yahoo mappings only when the ticker format is already known."""
    mappings: list[SymbolMapping] = []
    if venue == "GPW":
        for security in sorted(securities.values(), key=lambda item: item.asset_id):
            if not security.is_active or security.instrument_class.lower() not in {
                "stock",
                "equity",
            }:
                continue
            if not security.symbol.endswith(".WA"):
                raise ValueError(f"GPW equity lacks reviewed Yahoo .WA symbol: {security.symbol}")
            mappings.append(
                SymbolMapping(security.asset_id, "YFINANCE", security.symbol, EARLIEST_MAPPING_DATE)
            )
    mappings.extend(reviewed)
    # Permit a reviewed CSV to repeat the generated Yahoo mapping exactly.
    unique = list(dict.fromkeys(mappings))
    if not unique:
        raise ValueError("No active GPW equities or reviewed provider symbols selected")
    return unique


def read_existing_providers(cursor: Any) -> dict[str, int]:
    cursor.execute('SELECT "PROVIDER_ID", "PROVIDER_CODE" FROM "PROVIDERS"')
    result: dict[str, int] = {}
    used_ids: set[int] = set()
    for provider_id, raw_code in cursor.fetchall():
        code = str(raw_code).upper()
        if provider_id is None or code in result or int(provider_id) in used_ids:
            raise ValueError(f"Duplicate/invalid PROVIDERS registry: {code}")
        result[code] = int(provider_id)
        used_ids.add(int(provider_id))
    return result


def read_existing_mappings(cursor: Any) -> list[ExistingMapping]:
    cursor.execute(
        'SELECT "PROVIDER_SYMBOL_ID", "ASSET_ID", "PROVIDER_ID", "PROVIDER_SYMBOL", '
        '"VALID_FROM", "VALID_TO" FROM "PROVIDER_SYMBOLS"'
    )
    output: list[ExistingMapping] = []
    used_ids: set[int] = set()
    for mapping_id, asset_id, provider_id, symbol, start, end in cursor.fetchall():
        if mapping_id is None or int(mapping_id) in used_ids:
            raise ValueError("Duplicate or null PROVIDER_SYMBOL_ID")
        used_ids.add(int(mapping_id))
        start_date = _date_value(start)
        assert start_date is not None
        output.append(
            ExistingMapping(
                int(mapping_id),
                int(asset_id),
                int(provider_id),
                str(symbol),
                start_date,
                _date_value(end, nullable=True),
            )
        )
    return output


def _overlap(a_start: date, a_end: date | None, b_start: date, b_end: date | None) -> bool:
    return a_start <= (b_end or date.max) and b_start <= (a_end or date.max)


def plan_seed(
    current_providers: dict[str, int],
    current_mappings: list[ExistingMapping],
    requested: list[SymbolMapping],
) -> SeedPlan:
    """Plan only missing entries; fail closed on contradictory identifier mappings."""
    ids = dict(current_providers)
    next_provider_id = max(ids.values(), default=0)
    to_add_providers: list[tuple[int, Provider]] = []
    for provider in PROVIDERS:
        if provider.code not in ids:
            next_provider_id += 1
            ids[provider.code] = next_provider_id
            to_add_providers.append((next_provider_id, provider))
    next_mapping_id = max((item.mapping_id for item in current_mappings), default=0)
    planned: list[tuple[int, SymbolMapping]] = []
    existing_count = 0
    combined = list(current_mappings)
    for mapping in requested:
        pid = ids[mapping.provider_code]
        identical = False
        for old in combined:
            if old.provider_id != pid:
                continue
            if (
                old.asset_id == mapping.asset_id
                and old.provider_symbol == mapping.provider_symbol
                and old.valid_from <= mapping.valid_from
                and (old.valid_to or date.max) >= (mapping.valid_to or date.max)
            ):
                identical = True
                break
            if _overlap(old.valid_from, old.valid_to, mapping.valid_from, mapping.valid_to):
                if (
                    old.asset_id == mapping.asset_id
                    and old.provider_symbol != mapping.provider_symbol
                ):
                    raise ValueError(
                        f"Conflicting provider symbols for asset {mapping.asset_id} "
                        f"at provider {mapping.provider_code}"
                    )
                if (
                    old.provider_symbol == mapping.provider_symbol
                    and old.asset_id != mapping.asset_id
                ):
                    raise ValueError(
                        f"Provider symbol {mapping.provider_symbol} already maps to asset {old.asset_id}"
                    )
                if (
                    old.asset_id == mapping.asset_id
                    and old.provider_symbol == mapping.provider_symbol
                ):
                    raise ValueError("Overlapping validity periods for the same provider mapping")
        if identical:
            existing_count += 1
            continue
        next_mapping_id += 1
        planned.append((next_mapping_id, mapping))
        combined.append(
            ExistingMapping(
                next_mapping_id,
                mapping.asset_id,
                pid,
                mapping.provider_symbol,
                mapping.valid_from,
                mapping.valid_to,
            )
        )
    return SeedPlan(tuple(to_add_providers), tuple(planned), existing_count)


def seed_provider_symbols(
    connection: Any,
    *,
    venue: str = "GPW",
    csv_path: Path | None = None,
    write: bool = False,
    activate_alternatives: bool = False,
    skip_unregistered: bool = False,
) -> SeedPlan:
    """Seed Snowflake provider metadata and GPW mappings; default dry-run.

    Only DML in the write transaction. Never deletes or overwrites any mapping.
    Use a single seeder at a time: MAX(ID)+1 isn't a concurrent allocator.
    """
    venue = venue.strip().upper()
    if venue != "GPW":
        raise ValueError("Automatic symbol seeding currently supports GPW only")
    with closing(connection.cursor()) as cur:
        if write:
            cur.execute("BEGIN")
        try:
            securities = read_securities(cur, venue=venue)
            reviewed = (
                read_reviewed_csv(csv_path, securities, venue, skip_unregistered=skip_unregistered)
                if csv_path
                else []
            )
            requested = wanted_mappings(securities, venue=venue, reviewed=reviewed)
            providers = read_existing_providers(cur)
            existing = read_existing_mappings(cur)
            plan = plan_seed(providers, existing, requested)
            if activate_alternatives:
                # Explicit activation requested after importing reviewed mappings.
                active_codes = {"STOOQ", "BIZNESRADAR"}
                target_ids = {**providers, **{p.code: pid for pid, p in plan.providers}}
                for code in active_codes:
                    if not any(m.provider_code == code for m in requested):
                        raise ValueError(
                            f"No reviewed mapping for provider {code}; refusing activation"
                        )
                plan = SeedPlan(
                    plan.providers,
                    plan.mappings,
                    plan.existing_count,
                    tuple(target_ids[code] for code in sorted(active_codes)),
                )
            if write:
                for provider_id, provider in plan.providers:
                    cur.execute(
                        'INSERT INTO "PROVIDERS" '
                        '("PROVIDER_ID", "PROVIDER_CODE", "NAME", "PRIORITY", "IS_ACTIVE") '
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            provider_id,
                            provider.code,
                            provider.name,
                            provider.priority,
                            provider.is_active,
                        ),
                    )
                ids = dict(providers)
                ids.update({item.code: identifier for identifier, item in plan.providers})
                for identifier, mapping in plan.mappings:
                    cur.execute(
                        'INSERT INTO "PROVIDER_SYMBOLS" '
                        '("PROVIDER_SYMBOL_ID", "ASSET_ID", "PROVIDER_ID", '
                        '"PROVIDER_SYMBOL", "VALID_FROM", "VALID_TO") '
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            identifier,
                            mapping.asset_id,
                            ids[mapping.provider_code],
                            mapping.provider_symbol,
                            mapping.valid_from,
                            mapping.valid_to,
                        ),
                    )
                for provider_id in plan.activate_ids:
                    cur.execute(
                        'UPDATE "PROVIDERS" SET "IS_ACTIVE"=TRUE WHERE "PROVIDER_ID"=%s',
                        (provider_id,),
                    )
                cur.execute("COMMIT")
        except Exception:
            if write:
                cur.execute("ROLLBACK")
            raise
    return plan
