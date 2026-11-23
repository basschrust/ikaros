"""Populate a new local DuckDB from reviewed, LOCAL universe/reference files.

No Snowflake calls, no cloud database dependency, no price imports. All writes
are limited to reference tables and provider-symbol mappings, preserving the
candidate-first publication boundary.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from database.candidate_catalog import register_foreign_yahoo_mappings
from database.market_quote_seed import seed_mappings

VENUE_FILES = {
    "GPW": "gpw_stocks.csv",
    "US": "us_sp500.csv",
    "PSE": "europe_pse.csv",
    "XETRA": "europe_xetra.csv",
    "PARIS": "europe_paris.csv",
    "LSE": "europe_lse.csv",
    "BSE": "europe_bse.csv",
}
VENUE_CURRENCY = {
    "GPW": "PLN",
    "US": "USD",
    "PSE": "CZK",
    "XETRA": "EUR",
    "PARIS": "EUR",
    "LSE": "GBP",
    "BSE": "HUF",
}
SUFFIX = {"GPW": ".WA", "PSE": ".PR", "XETRA": ".DE", "PARIS": ".PA", "LSE": ".L", "BSE": ".BD"}
PROVIDERS = {
    "YFINANCE": ("Yahoo Finance", 10),
    "STOOQ": ("Stooq", 20),
    "BIZNESRADAR": ("BiznesRadar", 30),
}


@dataclass(frozen=True)
class BootstrapReport:
    currencies: int
    currency_pairs: int
    securities: int
    provider_symbols: int
    derivative_contracts: int
    rate_series: int
    missing_files: tuple[str, ...]


def _rows(conn: Any, query: str, params: tuple[object, ...] = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def _insert(conn: Any, sql: str, params: tuple[object, ...]) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    return [
        {str(k).lower().strip(): str(v or "").strip() for k, v in row.items()} for row in records
    ]


def _add_currency_reference(conn: Any, *, config_path: Path) -> tuple[int, int]:
    with config_path.open(encoding="utf-8") as stream:
        settings = yaml.safe_load(stream)
    ids: dict[str, int] = {
        str(code): int(identifier)
        for identifier, code in _rows(conn, 'SELECT "CURRENCY_ID", "CODE" FROM "CURRENCIES"')
    }
    nxt = max(ids.values(), default=0)
    added_currencies = 0
    for spec in settings.get("currencies", []):
        code = str(spec["code"]).upper()
        if code in ids:
            continue
        nxt += 1
        _insert(
            conn,
            'INSERT INTO "CURRENCIES" ("CURRENCY_ID","CODE","SYMBOL","NAME","MINOR_UNIT","IS_ACTIVE") VALUES (?,?,?,?,?,TRUE)',
            (nxt, code, code, str(spec.get("name", code)), int(spec.get("minor_unit", 2))),
        )
        ids[code] = nxt
        added_currencies += 1
    existing = {
        str(symbol): (int(identifier), int(base), int(quote))
        for identifier, symbol, base, quote in _rows(
            conn,
            'SELECT "CURRENCY_PAIR_ID","SYMBOL","BASE_CURRENCY_ID","QUOTE_CURRENCY_ID" FROM "CURRENCY_PAIRS"',
        )
    }
    pair_id = max((row[0] for row in existing.values()), default=0)
    added_pairs = 0
    for pair in settings.get("currency_pairs", []):
        symbol = str(pair["symbol"])
        base = ids[str(pair["base"]).upper()]
        quote = ids[str(pair["quote"]).upper()]
        if symbol in existing:
            if existing[symbol][1:] != (base, quote):
                raise ValueError(f"Currency pair {symbol} reference mismatch")
            continue
        pair_id += 1
        _insert(
            conn,
            'INSERT INTO "CURRENCY_PAIRS" ("CURRENCY_PAIR_ID","BASE_CURRENCY_ID","QUOTE_CURRENCY_ID","SYMBOL","IS_ACTIVE") VALUES (?,?,?,?,TRUE)',
            (pair_id, base, quote, symbol),
        )
        added_pairs += 1
    return added_currencies, added_pairs


def _add_securities(conn: Any, *, path: Path, venue: str) -> int:
    records = _csv(path)
    current = {
        str(symbol): (int(identifier), str(existing_venue), str(currency))
        for symbol, identifier, existing_venue, currency in _rows(
            conn,
            'SELECT "SYMBOL","ASSET_ID","VENUE","CURRENCY" FROM "SECURITIES"',
        )
    }
    next_id = max((value[0] for value in current.values()), default=0)
    count = 0
    seen = set()
    for item in records:
        symbol = (item.get("symbol") or item.get("ticker") or "").upper().strip()
        name = (item.get("name") or symbol).strip()
        if not symbol or not name:
            raise ValueError(f"Missing ticker/name in {path}")
        if venue == "US":
            symbol = symbol.replace(".", "-")
        if venue in SUFFIX and not symbol.endswith(SUFFIX[venue]):
            symbol += SUFFIX[venue]
        if symbol in seen:
            raise ValueError(f"Duplicate {venue} symbol {symbol} in {path}")
        seen.add(symbol)
        if symbol in current:
            if current[symbol][1:] != (venue, VENUE_CURRENCY[venue]):
                raise ValueError(f"Existing SECURITIES reference mismatch for {symbol}")
            continue
        next_id += 1
        _insert(
            conn,
            'INSERT INTO "SECURITIES" ("ASSET_ID","INSTRUMENT_CLASS","MARKET_TYPE","NAME","SYMBOL","CURRENCY","CALENDAR_ID","VENUE","IS_ACTIVE") VALUES (?,?,?,?,?,?,?,?,TRUE)',
            (next_id, "stock", "continuous", name, symbol, VENUE_CURRENCY[venue], 0, venue),
        )
        count += 1
        current[symbol] = (next_id, venue, VENUE_CURRENCY[venue])
    return count


def _add_providers(conn: Any) -> dict[str, int]:
    existing = {
        str(code): int(identifier)
        for identifier, code in _rows(conn, 'SELECT "PROVIDER_ID","PROVIDER_CODE" FROM "PROVIDERS"')
    }
    next_id = max(existing.values(), default=0)
    for code, (name, priority) in PROVIDERS.items():
        if code in existing:
            continue
        next_id += 1
        _insert(
            conn,
            'INSERT INTO "PROVIDERS" ("PROVIDER_ID","PROVIDER_CODE","NAME","PRIORITY","IS_ACTIVE") VALUES (?,?,?,?,TRUE)',
            (next_id, code, name, priority),
        )
        existing[code] = next_id
    return existing


def _add_gpw_yahoo_mappings(conn: Any, *, provider_id: int) -> int:
    rows = _rows(
        conn,
        'SELECT "ASSET_ID","SYMBOL" FROM "SECURITIES" WHERE "VENUE"=\'GPW\' AND "IS_ACTIVE"=TRUE',
    )
    existing = {
        (int(asset), int(pid)): str(alias)
        for asset, pid, alias in _rows(
            conn,
            'SELECT "ASSET_ID","PROVIDER_ID","PROVIDER_SYMBOL" FROM "PROVIDER_SYMBOLS"',
        )
    }
    current_max = _rows(
        conn, 'SELECT COALESCE(MAX("PROVIDER_SYMBOL_ID"),0) FROM "PROVIDER_SYMBOLS"'
    )
    next_id = int(current_max[0][0])
    created = 0
    for asset_id, alias in rows:
        key = (int(asset_id), provider_id)
        if key in existing:
            if existing[key] != alias:
                raise ValueError(f"Conflicting GPW Yahoo mapping for {alias}")
            continue
        next_id += 1
        _insert(
            conn,
            'INSERT INTO "PROVIDER_SYMBOLS" ("PROVIDER_SYMBOL_ID","ASSET_ID","PROVIDER_ID","PROVIDER_SYMBOL","VALID_FROM") VALUES (?,?,?,?,CAST(? AS DATE))',
            (next_id, asset_id, provider_id, alias, "1990-01-01"),
        )
        created += 1
    return created


def _add_contracts(conn: Any, *, path: Path | None) -> int:
    if path is None:
        return 0
    present = {
        str(row[0]): (str(row[1]), str(row[2]), str(row[3])[:10])
        for row in _rows(
            conn,
            'SELECT "CONTRACT_ID","INSTRUMENT_TYPE","UNDERLYING_SYMBOL","EXPIRY_DATE" FROM "DERIVATIVE_CONTRACTS"',
        )
    }
    created = 0
    for item in _csv(path):
        symbol = item["contract_id"]
        kind = item["instrument_type"].upper()
        underlying = item["underlying_symbol"]
        expiry = item["expiry_date"]
        if kind not in ("FUTURE", "OPTION"):
            raise ValueError(f"Invalid contract type {kind!r}")
        if symbol in present:
            if present[symbol] != (kind, underlying, expiry):
                raise ValueError(f"Conflicting derivative metadata for {symbol}")
            continue
        _insert(
            conn,
            'INSERT INTO "DERIVATIVE_CONTRACTS" ("CONTRACT_ID","INSTRUMENT_TYPE","UNDERLYING_SYMBOL","EXPIRY_DATE") VALUES (?,?,?,CAST(? AS DATE))',
            (symbol, kind, underlying, expiry),
        )
        created += 1
    return created


def _add_rate_series(conn: Any, *, path: Path | None) -> int:
    if path is None:
        # Reuse the existing curated local provider catalogs. No Snowflake or
        # network access is necessary to register the available series.
        from run.backfill_rates import select_series
        from database.candidate_observations import register_rate_series

        specs, _sources = select_series(None)
        before = int(_rows(conn, 'SELECT COUNT(*) FROM "RATE_SERIES"')[0][0])
        register_rate_series(conn, specs.values(), backend="duckdb", write=True)
        after = int(_rows(conn, 'SELECT COUNT(*) FROM "RATE_SERIES"')[0][0])
        return after - before
    existing = {row[0] for row in _rows(conn, 'SELECT "SERIES_ID" FROM "RATE_SERIES"')}
    count = 0
    for item in _csv(path):
        symbol = item["series_id"]
        if symbol in existing:
            continue
        fields = (
            "series_id",
            "currency",
            "source",
            "curve_type",
            "tenor",
            "source_key",
            "compounding",
            "day_count",
        )
        _insert(
            conn,
            'INSERT INTO "RATE_SERIES" ("SERIES_ID","CURRENCY","SOURCE","CURVE_TYPE","TENOR","SOURCE_KEY","COMPOUNDING","DAY_COUNT") VALUES (?,?,?,?,?,?,?,?)',
            tuple(item.get(field, "") for field in fields),
        )
        count += 1
    return count


def bootstrap(
    connection: Any,
    *,
    project_root: Path,
    contracts_csv: Path | None = None,
    rates_csv: Path | None = None,
    write: bool,
) -> BootstrapReport:
    """Seed local reference data. Idempotent; never writes authoritative prices."""
    data_dir = project_root / "data"
    missing = tuple(
        f"data/{filename} ({venue})"
        for venue, filename in VENUE_FILES.items()
        if not (data_dir / filename).is_file()
    )
    if not write:
        return BootstrapReport(0, 0, 0, 0, 0, 0, missing)
    currencies, pairs = _add_currency_reference(
        connection, config_path=project_root / "config" / "currencies.yaml"
    )
    security_count = 0
    for venue, filename in VENUE_FILES.items():
        path = data_dir / filename
        if path.is_file():
            security_count += _add_securities(connection, path=path, venue=venue)
    providers = _add_providers(connection)
    mappings = _add_gpw_yahoo_mappings(connection, provider_id=providers["YFINANCE"])
    # Foreign Yahoo mappings are already implemented in candidate_catalog.
    mappings += register_foreign_yahoo_mappings(connection, backend="duckdb", write=True)
    contracts = _add_contracts(connection, path=contracts_csv)
    rates = _add_rate_series(connection, path=rates_csv)
    assigned = seed_mappings(connection, backend="duckdb", write=True)
    mappings += sum(assigned.values())
    return BootstrapReport(currencies, pairs, security_count, mappings, contracts, rates, missing)
