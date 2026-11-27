"""Reconcile old GPW WIG20 derivative venues from retained listing evidence.

This is a metadata-only repair. A contract is never classified as GPW solely
because of its identifier, underlying, price provider, or ingestion dataset.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Exact listing pages queried by providers.derivatives.wig20_listings.discover.
# Generic market-data/standards pages are not evidence of a particular listing.
_LISTINGS = {
    "FUTURE": frozenset(
        {
            ("www.bankier.pl", "/gielda/notowania/futures"),
            ("www.biznesradar.pl", "/gielda/pochodne_kontrakty"),
        }
    ),
    "OPTION": frozenset(
        {
            ("www.bankier.pl", "/gielda/notowania/opcje"),
            ("www.biznesradar.pl", "/gielda/pochodne_opcje"),
        }
    ),
}


@dataclass(frozen=True)
class VenuePlan:
    """Immutable, fully validated update plan; no writes while collecting it."""

    eligible: tuple[str, ...]
    already_gpw: tuple[str, ...]
    unresolved: tuple[str, ...]
    foreign: tuple[str, ...]
    source_by_contract: dict[str, str]


def _reference_url(*, evidence: str, kind: str) -> str | None:
    """Accept only known, kind-specific GPW contract listing pages."""
    for token in evidence.split("|"):
        try:
            parsed = urlsplit(token.strip())
        except ValueError:
            continue
        if parsed.scheme.lower() != "https":
            continue
        # Compare host and path exactly: no lookalike domains, custom ports,
        # userinfo, query strings, or unrelated finance pages.
        address = (parsed.netloc.lower(), parsed.path.rstrip("/").lower())
        if address in _LISTINGS[kind] and not parsed.query and not parsed.fragment:
            return f"https://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return None


def read_discovery(*, path: Path) -> dict[str, dict[str, str]]:
    """Read an archived discovery manifest, even one predating the VENUE column."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {str(x).strip().lower() for x in reader.fieldnames or []}
        required = {
            "contract_id",
            "symbol",
            "instrument_type",
            "underlying_symbol",
            "expiry_date",
            "source",
        }
        if not required <= fields:
            raise ValueError(f"Discovery manifest missing columns: {sorted(required - fields)}")
        found: dict[str, dict[str, str]] = {}
        for row in reader:
            item = {str(k).strip().lower(): str(v or "").strip() for k, v in row.items()}
            contract_id = item["contract_id"].upper()
            if not contract_id or contract_id in found:
                raise ValueError(f"Blank or duplicate contract ID in {path}: {contract_id!r}")
            found[contract_id] = item
    return found


def _verify_discovery(*, row: dict[str, str], stored: tuple[Any, ...]) -> None:
    """A saved manifest must not silently contradict the registered contract."""
    contract_id, _venue, kind, underlying, expiry, strike, side, _source = stored
    cid = str(contract_id).upper()
    expected = {
        "contract_id": cid,
        "symbol": cid,
        "instrument_type": str(kind or "").upper(),
        "underlying_symbol": str(underlying or "").upper(),
        "expiry_date": str(expiry)[:10],
    }
    for field, value in expected.items():
        if str(row.get(field, "")).upper() != value:
            raise ValueError(
                f"Conflicting discovery metadata for {cid}.{field}: "
                f"registered={value!r} discovered={row.get(field)!r}"
            )
    venue = row.get("venue", "").upper()
    if venue not in ("", "GPW"):
        raise ValueError(f"Conflicting discovery venue for {cid}: {venue!r}")
    if kind == "OPTION":
        if strike is not None and row.get("strike"):
            from decimal import Decimal

            if Decimal(str(strike)) != Decimal(row["strike"]):
                raise ValueError(f"Conflicting discovery strike for {cid}")
        if side and row.get("option_side") and str(side).upper() != row["option_side"].upper():
            raise ValueError(f"Conflicting discovery option side for {cid}")


def plan_gpw_venue_repair(*, connection: Any, discovered: dict[str, dict[str, str]]) -> VenuePlan:
    """Find verified WIG20 contracts with missing venue, without mutating DB."""
    rows = list(
        connection.execute(
            'SELECT "CONTRACT_ID", "VENUE", "INSTRUMENT_TYPE", '
            '"UNDERLYING_SYMBOL", "EXPIRY_DATE", "STRIKE", "OPTION_SIDE", "SOURCE" '
            'FROM "DERIVATIVE_CONTRACTS" WHERE "UNDERLYING_SYMBOL"=? '
            "AND \"INSTRUMENT_TYPE\" IN ('FUTURE','OPTION') "
            'ORDER BY "CONTRACT_ID"',
            ("WIG20",),
        ).fetchall()
    )
    known = {str(r[0]).upper(): r for r in rows}
    # Unknown IDs in a stale manifest are not imported, but conflicting metadata
    # of IDs that are present is a hard stop, before any database modifications.
    eligible: list[str] = []
    already: list[str] = []
    unresolved: list[str] = []
    foreign: list[str] = []
    evidence: dict[str, str] = {}
    for cid, record in known.items():
        _cid, raw_venue, kind, _underlying, _expiry, _strike, _side, source = record
        kind = str(kind).upper()
        manifest = discovered.get(cid)
        if manifest is not None:
            _verify_discovery(row=manifest, stored=record)
        # Prefer the retained discovery row; stored source is also accepted if
        # it already carries a qualifying exact contract-listing URL.
        # STANDARD_DERIVED is added only by the exact-symbol listing discovery
        # pipeline; a bare link to a generic listings page is not sufficient.
        manifest_source = manifest.get("source", "") if manifest is not None else ""
        stored_source = str(source or "")
        evidence_candidates = (manifest_source, stored_source)
        proof = None
        for candidate_source in evidence_candidates:
            tokens = {x.strip().upper() for x in candidate_source.split("|")}
            if "STANDARD_DERIVED" in tokens:
                proof = _reference_url(evidence=candidate_source, kind=kind)
                if proof:
                    break
        venue = str(raw_venue or "").strip().upper()
        if venue == "GPW":
            already.append(cid)
        elif venue:
            foreign.append(cid)
            if proof:
                raise ValueError(
                    f"Contract {cid} is registered on {venue}, but GPW discovery "
                    "evidence also exists. Resolve the conflict before any update."
                )
        elif proof:
            eligible.append(cid)
            evidence[cid] = proof
        else:
            unresolved.append(cid)
    return VenuePlan(
        eligible=tuple(eligible),
        already_gpw=tuple(already),
        unresolved=tuple(unresolved),
        foreign=tuple(foreign),
        source_by_contract=evidence,
    )


def apply_gpw_venue_repair(*, connection: Any, plan: VenuePlan) -> int:
    """Only set an empty VENUE, using conditional updates in one transaction."""
    if not plan.eligible:
        return 0
    connection.execute("BEGIN")
    try:
        for contract_id in plan.eligible:
            connection.execute(
                'UPDATE "DERIVATIVE_CONTRACTS" SET "VENUE"=? '
                'WHERE "CONTRACT_ID"=? AND ("VENUE" IS NULL OR TRIM("VENUE")=\'\')',
                ("GPW", contract_id),
            )
        count = int(
            connection.execute(
                'SELECT COUNT(*) FROM "DERIVATIVE_CONTRACTS" '
                'WHERE "VENUE"=? AND "CONTRACT_ID" IN ('
                + ",".join("?" for _ in plan.eligible)
                + ")",
                ("GPW", *plan.eligible),
            ).fetchone()[0]
        )
        if count != len(plan.eligible):
            raise RuntimeError(
                f"GPW venue repair expected {len(plan.eligible)} rows, got {count}; rolled back"
            )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return count
