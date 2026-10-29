"""Conservative FW20 contract registration from discovered GPW candidate symbols.

Contract identifiers and multiplier are verified against published GPW standards;
series existence comes from discovered candidates. No price data is generated.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

FW20_RE = re.compile(r"^FW20(?P<month>[HMUZ])(?P<year>\d{2})20$")
MONTHS = {"H": 3, "M": 6, "U": 9, "Z": 12}


def standard_expiry(symbol: str) -> date | None:
    match = FW20_RE.fullmatch(symbol)
    if match is None:
        return None
    year = 2000 + int(match.group("year"))
    month = MONTHS[match.group("month")]
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def candidate_spec(symbol: str, reported_expiry: date | None, *, as_of: date) -> dict | None:
    """Return an eligible FW20 specification or None.

    An exchange exception/holiday can change actual expiry; standard third
    Friday is a provisional policy until official series expiry is verified.
    Source date may be previous day (Bankier display); require same month/year.
    """
    expiry = standard_expiry(symbol)
    if expiry is None or expiry < as_of or reported_expiry is None:
        return None
    if (reported_expiry.year, reported_expiry.month) != (expiry.year, expiry.month):
        return None
    if abs((reported_expiry - expiry).days) > 3:
        return None
    return dict(
        contract_id=symbol,
        symbol=symbol,
        instrument_type="FUTURE",
        underlying_symbol="WIG20",
        expiry_date=expiry,
        currency="PLN",
        multiplier=20.0,
        strike=None,
        option_side=None,
        source="gpw_standard+bankier_discovery",
    )


SELECT_CANDIDATES = '''SELECT "SYMBOL", "EXPIRY_DATE", "SOURCE" FROM "DERIVATIVE_CANDIDATES"
WHERE "INSTRUMENT_TYPE" = 'FUTURE' AND "SYMBOL" LIKE 'FW20%'
AND "SOURCE" = 'bankier' ORDER BY "SYMBOL"'''

SELECT_CONTRACT = """SELECT "SYMBOL", "INSTRUMENT_TYPE", "UNDERLYING_SYMBOL",
"EXPIRY_DATE", "CURRENCY", "MULTIPLIER", "STRIKE", "OPTION_SIDE"
FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID" = %s"""

INSERT_CONTRACT = """INSERT INTO "DERIVATIVE_CONTRACTS"
("CONTRACT_ID", "SYMBOL", "INSTRUMENT_TYPE", "UNDERLYING_SYMBOL", "EXPIRY_DATE",
 "CURRENCY", "MULTIPLIER", "STRIKE", "OPTION_SIDE", "SOURCE")
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

UPDATE_STATUS = """UPDATE "DERIVATIVE_CANDIDATES" SET "REVIEW_STATUS" = 'REGISTERED'
WHERE "SYMBOL" = %s AND "SOURCE" = 'bankier' AND "INSTRUMENT_TYPE" = 'FUTURE' """


def matching(existing, spec: dict) -> bool:
    expected = (
        spec["symbol"],
        spec["instrument_type"],
        spec["underlying_symbol"],
        spec["expiry_date"],
        spec["currency"],
        spec["multiplier"],
        None,
        None,
    )
    # Source can differ (e.g. previously imported BiznesRadar data).
    return (
        existing[0:5] == expected[0:5]
        and float(existing[5]) == expected[5]
        and existing[6] is None
        and existing[7] is None
    )


def register(connection, *, as_of: date, write: bool = False) -> list[tuple[str, str]]:
    """Promote eligible WIG20 series using a single transaction per series.

    Existing matching contracts are left untouched. Conflicts and unverified
    candidates are reported, not modified. Requires initialized tables.
    """
    with connection.cursor() as cur:
        cur.execute(SELECT_CANDIDATES)
        candidates = cur.fetchall()
    outcome = []
    for symbol, reported_expiry, _source in candidates:
        spec = candidate_spec(symbol, reported_expiry, as_of=as_of)
        if spec is None:
            outcome.append((symbol, "REVIEW_REQUIRED"))
            continue
        with connection.cursor() as cur:
            cur.execute(SELECT_CONTRACT, (symbol,))
            rows = cur.fetchall()
        if len(rows) > 1:
            outcome.append((symbol, "DUPLICATE_EXISTING"))
            continue
        if rows and not matching(rows[0], spec):
            outcome.append((symbol, "CONFLICT"))
            continue
        status = "ALREADY_REGISTERED" if rows else "READY"
        if write:
            with connection.cursor() as cur:
                try:
                    cur.execute("BEGIN")
                    if not rows:
                        cur.execute(INSERT_CONTRACT, tuple(spec.values()))
                    cur.execute(UPDATE_STATUS, (symbol,))
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
            status = "EXISTING" if rows else "REGISTERED"
        outcome.append((symbol, status))
    return outcome
