"""NBP Table A reference-rate backfill helpers.

NBP publishes daily reference mid rates, not market OHLC. For each listed
currency, rates are expressed in PLN per *quoted number of units*.
"""

from __future__ import annotations

from datetime import date, timedelta
import math
import re
import logging

LOG = logging.getLogger(__name__)

# Published NBP Table A quotation units (see NBP official currency table).
QUOTE_UNITS = {
    "HUF": 100,
    "JPY": 100,
    "ISK": 100,
    "CLP": 100,
    "IDR": 10000,
    "INR": 100,
    "KRW": 100,
}
PAIR = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")


def windows(start: date, end: date, max_days: int = 90):
    """Inclusive chunks smaller than the NBP API's 93-day range cap."""
    if start > end or max_days < 1 or max_days > 93:
        raise ValueError("Invalid date range or API window size")
    current = start
    while current <= end:
        last = min(end, current + timedelta(days=max_days - 1))
        yield current, last
        current = last + timedelta(days=1)


def parse_table(payload: list[dict]) -> dict[date, dict[str, float]]:
    """Normalized PLN-per-unit reference rates, by NBP effective date."""
    result = {}
    for item in payload:
        day = date.fromisoformat(item["effectiveDate"])
        if day in result:
            raise ValueError(f"Duplicate NBP effective date {day}")
        rates = {"PLN": 1.0}
        # Some historical NBP tables reuse a code within one effective date.
        # Never guess which of conflicting quotations represents that currency.
        ambiguous: set[str] = set()
        identical_duplicates: set[str] = set()
        for rate in item["rates"]:
            code = str(rate["code"]).upper()
            mid = float(rate["mid"])
            per_unit = mid / QUOTE_UNITS.get(code, 1)
            if not math.isfinite(per_unit) or per_unit <= 0:
                raise ValueError(f"Invalid NBP mid for {code} {day}")
            if code == "PLN":
                raise ValueError(f"Unexpected PLN base currency in NBP rates on {day}")
            if code in ambiguous:
                continue
            if code in rates:
                # Treat truly identical repeats as one observation; conflicting
                # rates are unsafe to use in direct or synthetic FX crosses.
                if math.isclose(rates[code], per_unit, rel_tol=1e-12, abs_tol=0):
                    identical_duplicates.add(code)
                else:
                    ambiguous.add(code)
                    rates.pop(code)
                continue
            rates[code] = per_unit
        if ambiguous:
            LOG.warning(
                "NBP %s: ambiguous duplicate codes %s; skipping affected currencies",
                day,
                ", ".join(sorted(ambiguous)),
            )
        if identical_duplicates - ambiguous:
            LOG.debug(
                "NBP %s: ignored identical repeated codes %s",
                day,
                ", ".join(sorted(identical_duplicates - ambiguous)),
            )
        result[day] = rates
    return result


def cross_rows(daily: dict[date, dict[str, float]], symbols: list[str]):
    """Generate close-only rows for registered pairs; skip missing currencies."""
    specs = []
    for symbol in dict.fromkeys(symbols):
        match = PAIR.fullmatch(symbol)
        if match is None or match.group(1) == match.group(2):
            raise ValueError(f"Invalid FX symbol: {symbol}")
        specs.append((symbol, *match.groups()))
    rows = []
    for day in sorted(daily):
        rates = daily[day]
        for symbol, base, quote in specs:
            if base not in rates or quote not in rates:
                continue
            close = rates[base] / rates[quote]
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f"Invalid cross {symbol} on {day}")
            rows.append(
                (
                    symbol,
                    day,
                    close,
                    f"NBP_TABLE_A_REFERENCE_MID; cross PLN-per-unit {base}/{quote}",
                )
            )
    return rows
