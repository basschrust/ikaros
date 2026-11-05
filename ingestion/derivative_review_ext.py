"""Review classification for derivative candidates, without inferring trade specs."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from ingestion.derivatives_registry import review


def review_candidate(
    symbol: str, instrument_type: str, expiry: date | None, *, as_of: date | None = None
) -> dict:
    symbol = symbol.strip().upper()
    if instrument_type == "FUTURE":
        return asdict(review(symbol, expiry, as_of=as_of))
    if instrument_type == "OPTION" and symbol.startswith("OW20"):
        reasons = [
            "Option strike, side, multiplier and exercise terms require verified exchange specification"
        ]
        if expiry is None:
            reasons.append("Listing did not provide expiry")
        if expiry is not None and as_of is not None and expiry < as_of:
            reasons.append("Expired at review date")
        return dict(
            symbol=symbol,
            family="OW20",
            category="INDEX_OPTION_CANDIDATE",
            underlying_hint="WIG20",
            contract_month=expiry.strftime("%Y-%m") if expiry else None,
            reported_expiry=expiry,
            third_friday=None,
            expiry_consistent=None,
            status="REVIEW",
            reason="; ".join(reasons),
        )
    return dict(
        symbol=symbol,
        family=None,
        category="UNKNOWN_OPTION",
        underlying_hint=None,
        contract_month=None,
        reported_expiry=expiry,
        third_friday=None,
        expiry_consistent=None,
        status="REVIEW",
        reason="Unsupported option family; verify manually",
    )
