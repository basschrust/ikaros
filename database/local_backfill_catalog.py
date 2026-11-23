"""Registered, locally actionable instruments for DuckDB historical backfills.

This module intentionally issues only read queries; all observations continue
through the existing candidate staging and independent promotion processes.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

EQUITY_VENUES = frozenset({"GPW", "US", "PSE", "XETRA", "PARIS", "LSE", "BSE"})
BACKFILL_DATASETS = (
    "FX",
    "GPW",
    "US",
    "PSE",
    "XETRA",
    "PARIS",
    "LSE",
    "BSE",
    "WIG20",
    "RATES",
    "DERIVATIVES",
)


@dataclass(frozen=True)
class RegisteredInstrument:
    symbol: str
    valid_from: date | None = None
    expiry: date | None = None


def _date(*, value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def registered_instruments(*, connection: Any, dataset: str) -> list[RegisteredInstrument]:
    """List currently registered, candidate-mapped series for one domain.

    Derivatives include expired contracts so historic 2025 windows remain
    eligible when those contracts are explicitly registered.
    """
    if dataset not in BACKFILL_DATASETS:
        raise ValueError(f"Unsupported local backfill dataset: {dataset}")

    params: tuple[object, ...] = ()
    if dataset in EQUITY_VENUES:
        sql = '''SELECT s."SYMBOL", MIN(ps."VALID_FROM"), NULL
                 FROM "SECURITIES" s
                 JOIN "PROVIDER_SYMBOLS" ps ON ps."ASSET_ID"=s."ASSET_ID"
                 JOIN "PROVIDERS" p ON p."PROVIDER_ID"=ps."PROVIDER_ID"
                 WHERE s."VENUE"=? AND COALESCE(s."IS_ACTIVE", TRUE)=TRUE
                   AND COALESCE(ps."IS_ACTIVE", TRUE)=TRUE
                   AND COALESCE(p."IS_ACTIVE", TRUE)=TRUE
                 GROUP BY s."SYMBOL" ORDER BY s."SYMBOL"'''
        params = (dataset,)
    elif dataset == "FX":
        sql = '''SELECT "SYMBOL", NULL, NULL FROM "CURRENCY_PAIRS"
                 WHERE "IS_ACTIVE"=TRUE ORDER BY "SYMBOL"'''
    elif dataset == "RATES":
        sql = '''SELECT "SERIES_ID", NULL, NULL FROM "RATE_SERIES"
                 WHERE "IS_ACTIVE"=TRUE ORDER BY "SERIES_ID"'''
    elif dataset == "WIG20":
        sql = '''SELECT "INDEX_SYMBOL", MIN("VALID_FROM"), NULL
                 FROM "INDEX_PROVIDER_SYMBOLS"
                 WHERE "IS_ACTIVE"=TRUE AND "INDEX_SYMBOL"='WIG20'
                 GROUP BY "INDEX_SYMBOL" ORDER BY "INDEX_SYMBOL"'''
    else:
        sql = '''SELECT c."CONTRACT_ID", MIN(m."VALID_FROM"), c."EXPIRY_DATE"
                 FROM "DERIVATIVE_CONTRACTS" c
                 JOIN "DERIVATIVE_PROVIDER_SYMBOLS" m
                   ON m."CONTRACT_ID"=c."CONTRACT_ID"
                 WHERE c."INSTRUMENT_TYPE" IN ('FUTURE', 'OPTION')
                   AND m."IS_ACTIVE"=TRUE
                 GROUP BY c."CONTRACT_ID", c."EXPIRY_DATE"
                 ORDER BY c."CONTRACT_ID"'''

    with closing(connection.cursor()) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [
        RegisteredInstrument(str(symbol), _date(value=first), _date(value=expiry))
        for symbol, first, expiry in rows
    ]
