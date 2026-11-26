"""Seed versioned WIG20 reference data without bypassing price promotion."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from database.daedalus_reference import ensure_reference_schema
from database.local_bootstrap import _add_gpw_yahoo_mappings, _add_providers, _add_securities
from database.market_quote_seed import seed_mappings
from database.provider_symbols_seed import (
    plan_seed,
    read_existing_mappings,
    read_existing_providers,
    read_reviewed_csv,
    read_securities,
)
from run.import_local_daedalus_inputs import (
    CONTRACT_FIELDS,
    WEIGHT_FIELDS,
    read_csv,
    store_contracts,
    store_weights,
    validate_contracts,
    validate_weights,
)


def seed(*, connection: Any, project_root: Path, target: date) -> tuple[str, ...]:
    """Keep user data; load only dated reference rows with verified provenance."""
    data = project_root / "data"
    snapshot, weights = validate_weights(
        rows=read_csv(path=data / "wig20_gpw_official_sept2026.csv", fields=WEIGHT_FIELDS)
    )
    contracts = []
    for name in (
        "wig20_gpw_verified_contracts_2026_10.csv",
        "wig20_gpw_verified_options_extra.csv",
    ):
        contracts.extend(
            validate_contracts(rows=read_csv(path=data / name, fields=CONTRACT_FIELDS))
        )
    ensure_reference_schema(connection)
    connection.execute("BEGIN")
    try:
        _add_securities(connection, path=data / "gpw_stocks.wig20.csv", venue="GPW")
        providers = _add_providers(connection)
        _add_gpw_yahoo_mappings(connection, provider_id=providers["YFINANCE"])
        # Do not call seed_provider_symbols: its automatic Yahoo mapping has a
        # different validity start from local_bootstrap's existing mappings.
        with connection.cursor() as cur:
            securities = read_securities(cur, venue="GPW")
            reviewed = read_reviewed_csv(data / "provider_symbols_gpw_wig20.csv", securities, "GPW")
            existing_providers = read_existing_providers(cur)
            plan = plan_seed(existing_providers, read_existing_mappings(cur), reviewed)
            for identifier, mapping in plan.mappings:
                cur.execute(
                    'INSERT INTO "PROVIDER_SYMBOLS" ("PROVIDER_SYMBOL_ID", "ASSET_ID", '
                    '"PROVIDER_ID", "PROVIDER_SYMBOL", "VALID_FROM", "VALID_TO") '
                    "VALUES (?,?,?,?,?,?)",
                    (
                        identifier,
                        mapping.asset_id,
                        existing_providers[mapping.provider_code],
                        mapping.provider_symbol,
                        mapping.valid_from,
                        mapping.valid_to,
                    ),
                )
        # An independently imported snapshot at the same effective date is
        # accepted only if its actual constituents/weights match the bundle.
        existing = connection.execute(
            'SELECT "SECURITY_SYMBOL", "WEIGHT" FROM "INDEX_CONSTITUENTS" '
            'WHERE "INDEX_SYMBOL"=? AND "VALID_FROM"=?',
            ("WIG20", snapshot),
        ).fetchall()
        if existing:
            from decimal import Decimal

            actual = sorted((str(s), Decimal(str(w))) for s, w in existing)
            expected = sorted((x["symbol"], x["weight"]) for x in weights)
            if actual != expected:
                raise ValueError(
                    f"Existing WIG20 snapshot at {snapshot} conflicts with starter data"
                )
        else:
            store_weights(connection=connection, as_of=snapshot, items=weights)
        store_contracts(connection=connection, items=contracts)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    seed_mappings(connection, backend="duckdb", write=True)
    # Subsequent verified snapshots can be imported with the existing CLI.
    current = connection.execute(
        'SELECT "SECURITY_SYMBOL" FROM "INDEX_CONSTITUENTS" WHERE "INDEX_SYMBOL"=? '
        'AND "VALID_FROM"<=? AND ("VALID_TO" IS NULL OR "VALID_TO">=?) ORDER BY 1',
        ("WIG20", target, target),
    ).fetchall()
    symbols = tuple(str(row[0]) for row in current)
    if len(symbols) != 20 or len(set(symbols)) != 20:
        raise ValueError(f"No complete WIG20 snapshot for {target}; import reviewed membership")
    print(f"Reference data: 20 WIG20 constituents, {len(contracts)} starter contracts")
    return symbols
