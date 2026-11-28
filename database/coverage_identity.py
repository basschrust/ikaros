"""Resolve reporting identity from authoritative reference records.

DATASET is an ingestion route, not a financial instrument's venue or type.
This module deliberately fails closed on duplicate or unrecognized references.
Unknown derivative venues stay unknown; never infer an exchange from a ticker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class CoverageIdentity:
    venue: str | None
    instrument_type: str


_EQUITY_TYPES = {"STOCK", "EQUITY"}
_INDEX_MARKET = {"WIG20": "GPW"}  # Market association, not an execution venue.
_DERIVATIVE_TYPES = {"FUTURE", "OPTION"}

COVERAGE_FIELDS = (
    "venue",
    "instrument_type",
    "symbol",
    "accepted_sessions",
    "missing_sessions",
    "first",
    "last",
    "status",
    "listing",
    "source_observations",
    "rejections",
    "source_errors",
)


def _rows(*, connection: Any, query: str, symbols: Sequence[str]) -> list[tuple[Any, ...]]:
    if not symbols:
        return []
    placeholders = ",".join("?" for _ in symbols)
    return list(
        connection.execute(query.format(placeholders=placeholders), tuple(symbols)).fetchall()
    )


def _venue(value: object, *, symbol: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    if not code.replace("_", "").isalnum():
        raise ValueError(f"Invalid VENUE {code!r} for {symbol}")
    return code


def stock_identities(*, connection: Any, symbols: Sequence[str]) -> dict[str, CoverageIdentity]:
    """Use SECURITIES.VENUE and INSTRUMENT_CLASS, never script dataset names."""
    expected = set(symbols)
    records = _rows(
        connection=connection,
        query='SELECT "SYMBOL", "VENUE", "INSTRUMENT_CLASS" '
        'FROM "SECURITIES" WHERE "SYMBOL" IN ({placeholders})',
        symbols=sorted(expected),
    )
    found: dict[str, CoverageIdentity] = {}
    for key, raw_venue, raw_type in records:
        symbol = str(key)
        if symbol in found:
            raise ValueError(f"Ambiguous SECURITIES reference for {symbol}")
        venue = _venue(raw_venue, symbol=symbol)
        if venue is None:
            raise ValueError(f"Missing SECURITIES.VENUE for {symbol}")
        kind = str(raw_type or "").strip().upper()
        if kind not in _EQUITY_TYPES:
            raise ValueError(f"Expected stock/equity, got {kind!r} for {symbol}")
        found[symbol] = CoverageIdentity(venue, "EQUITY")
    if found.keys() != expected:
        raise ValueError(f"Missing stock reference rows: {sorted(expected - found.keys())}")
    return found


def derivative_identities(
    *, connection: Any, symbols: Sequence[str]
) -> dict[str, CoverageIdentity]:
    """Derivatives are resolved from contract metadata, including nullable VENUE.

    Null VENUE is preserved, exposed in coverage and reported as incomplete.
    It is not replaced with the venue of the underlying or source website.
    """
    expected = set(symbols)
    records = _rows(
        connection=connection,
        query='SELECT "CONTRACT_ID", "VENUE", "INSTRUMENT_TYPE" '
        'FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID" IN ({placeholders})',
        symbols=sorted(expected),
    )
    found: dict[str, CoverageIdentity] = {}
    for key, raw_venue, raw_type in records:
        symbol = str(key)
        if symbol in found:
            raise ValueError(f"Ambiguous DERIVATIVE_CONTRACTS reference for {symbol}")
        kind = str(raw_type or "").strip().upper()
        if kind not in _DERIVATIVE_TYPES:
            raise ValueError(f"Invalid derivative INSTRUMENT_TYPE {kind!r} for {symbol}")
        found[symbol] = CoverageIdentity(_venue(raw_venue, symbol=symbol), kind)
    if found.keys() != expected:
        raise ValueError(f"Missing contract reference rows: {sorted(expected - found.keys())}")
    return found


def index_identity(*, symbol: str) -> CoverageIdentity:
    """WIG20's GPW association is explicit; other indices require reference data."""
    if symbol not in _INDEX_MARKET:
        raise ValueError(f"No reviewed market association for index {symbol}")
    return CoverageIdentity(_INDEX_MARKET[symbol], "INDEX")


def reclassify_existing_coverage(
    *, connection: Any, rows: Sequence[dict[str, str]]
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Reclassify an older coverage.csv without network calls or price writes.

    Old DATASET values are used *only* to select the correct reference table
    and check consistency, never as the new VENUE. Preserve every other field.
    """
    legacy = {"GPW", "WIG20", "FUTURE", "OPTION", "FUTURES", "OPTIONS"}
    if any((row.get("dataset") or "").upper() not in legacy for row in rows):
        unknown = {row.get("dataset", "") for row in rows} - legacy
        raise ValueError(f"Unsupported legacy DATASET values: {sorted(unknown)}")
    equities = sorted({row["symbol"] for row in rows if row["dataset"].upper() == "GPW"})
    derivatives = sorted(
        {
            row["symbol"]
            for row in rows
            if row["dataset"].upper() in {"FUTURE", "OPTION", "FUTURES", "OPTIONS"}
        }
    )
    stock_meta = stock_identities(connection=connection, symbols=equities)
    contract_meta = derivative_identities(connection=connection, symbols=derivatives)
    normalized: list[dict[str, str]] = []
    missing_venues: set[str] = set()
    for row in rows:
        legacy_type = row["dataset"].upper()
        symbol = row["symbol"]
        if legacy_type == "GPW":
            identity = stock_meta[symbol]
        elif legacy_type == "WIG20":
            identity = index_identity(symbol=symbol)
        else:
            identity = contract_meta[symbol]
            expected = "FUTURE" if legacy_type.startswith("FUTURE") else "OPTION"
            if expected != identity.instrument_type:
                raise ValueError(
                    f"Conflicting legacy DATASET and registered type for {symbol}: "
                    f"{legacy_type} versus {identity.instrument_type}"
                )
        if identity.venue is None:
            missing_venues.add(symbol)
        normalized.append(
            {
                **{name: value for name, value in row.items() if name != "dataset"},
                "venue": identity.venue or "",
                "instrument_type": identity.instrument_type,
            }
        )
    return normalized, tuple(sorted(missing_venues))
