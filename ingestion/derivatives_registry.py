"""Review-only classification for GPW derivative discovery candidates.

Does not infer contractual specifications or promote records to DERIVATIVE_CONTRACTS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
          "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
CURRENCY = {"FCHF": "CHFPLN=X", "FEUR": "EURPLN=X",
            "FGBP": "GBPPLN=X", "FUSD": "USDPLN=X"}
INDEX = {"FW20": "WIG20", "FW40": "mWIG40"}
# The optional terminal '20' is specific to WIG20 contract identifiers.
PATTERN = re.compile(r"^(?P<family>F[A-Z0-9]{3})(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{2})(?P<suffix>20)?$")


@dataclass(frozen=True)
class CandidateReview:
    symbol: str
    family: str | None
    category: str
    underlying_hint: str | None
    contract_month: str | None
    reported_expiry: date | None
    third_friday: date | None
    expiry_consistent: bool | None
    status: str
    reason: str


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def review(symbol: str, expiry: date | None, *, as_of: date | None = None) -> CandidateReview:
    symbol = symbol.strip().upper()
    m = PATTERN.fullmatch(symbol)
    if not m:
        return CandidateReview(symbol, None, "UNKNOWN", None, None, expiry, None,
                               None, "REVIEW", "Unrecognized symbol syntax")
    family, letter, year_code, suffix = m.group("family", "month", "year", "suffix")
    year = 2000 + int(year_code)
    month = MONTHS[letter]
    kind = "INDEX" if family in INDEX else "CURRENCY" if family in CURRENCY else "EQUITY_CANDIDATE"
    if suffix is not None and family != "FW20":
        return CandidateReview(symbol, family, kind, None, f"{year:04d}-{month:02d}",
                               expiry, third_friday(year, month), False, "REVIEW",
                               "Unexpected numeric suffix")
    expected = third_friday(year, month)
    hint = INDEX.get(family) or CURRENCY.get(family)
    if kind == "EQUITY_CANDIDATE":
        hint = f"{family[1:]}.WA"  # Guess only: require explicit mapping verification.
    reasons = ["Underlying and multiplier not verified"]
    if expiry is None:
        reasons.append("Missing source expiry")
    elif (expiry.year, expiry.month) != (year, month):
        reasons.append("Source expiry outside encoded contract month")
    elif expiry != expected:
        reasons.append("Source expiry differs from standard third Friday; check GPW calendar")
    if as_of is not None and expiry is not None and expiry < as_of:
        reasons.append("Expired as of reference date")
    return CandidateReview(symbol, family, kind, hint, f"{year:04d}-{month:02d}",
                           expiry, expected, expiry == expected if expiry else None,
                           "REVIEW", "; ".join(reasons))
