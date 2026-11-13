"""Official US Treasury TIPS real par yields and Treasury-bill rate conventions.

These are published yields, NOT bid/ask quotes. Values are stored as decimals.
Treasury-bill bank discount and coupon-equivalent rates are separate series.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
import math
import re
from requests import Session

from ikaros_app.rates import RateSeries, percent_to_decimal

SOURCE_REAL = "UST_REAL"
SOURCE_BILL = "UST_BILL"
URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
REAL_YEARS = (5, 7, 10, 20, 30)
BILL_WEEKS = (4, 6, 8, 13, 17, 26, 52)


@dataclass(frozen=True)
class QuoteableRateObservation:
    series_id: str
    day: date
    rate: float
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self):
        for name in ("rate", "bid", "ask"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"Nonfinite {name}")
        # Yield bid/ask ordering depends on quote conventions; do not enforce
        # a price-like bid <= ask invariant on interest-rate quotes.


def configured_series() -> dict[str, RateSeries]:
    result = {}
    for tenor in REAL_YEARS:
        sid = f"UST_REAL_PAR_USD_{tenor}Y"
        result[sid] = RateSeries(
            sid,
            SOURCE_REAL,
            "USD",
            "GOV_REAL_PAR",
            f"{tenor}Y",
            "daily_treasury_real_yield_curve",
            "PUBLISHED_REAL_PAR_YIELD",
            None,
        )
    for weeks in BILL_WEEKS:
        for suffix, curve_type, compounding in (
            ("DISCOUNT", "GOV_BILL_DISCOUNT", "BANK_DISCOUNT"),
            ("COUPON", "GOV_BILL_COUPON_EQUIV", "COUPON_EQUIVALENT"),
        ):
            sid = f"UST_BILL_{suffix}_USD_{weeks}W"
            result[sid] = RateSeries(
                sid,
                SOURCE_BILL,
                "USD",
                curve_type,
                f"{weeks}W",
                "daily_treasury_bill_rates",
                compounding,
                None,
            )
    return result


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()


def _clean(cell: str, label: str) -> float | None:
    raw = cell.strip()
    return None if not raw or raw.upper() in {"N/A", "ND", "-"} else percent_to_decimal(raw, label)


def _unique(rows: list[QuoteableRateObservation]) -> list[QuoteableRateObservation]:
    by_key = {}
    for item in rows:
        key = (item.series_id, item.day)
        if key in by_key and by_key[key] != item:
            raise ValueError(f"Conflicting observation {key}")
        by_key[key] = item
    return [by_key[k] for k in sorted(by_key)]


def parse_real_csv(content: str, start: date, end: date) -> list[QuoteableRateObservation]:
    reader = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames or "Date" not in reader.fieldnames:
        raise ValueError("Treasury real-yield CSV missing Date")
    recognized = {
        col: f"UST_REAL_PAR_USD_{tenor}Y"
        for col in reader.fieldnames
        for tenor in REAL_YEARS
        if col.strip().upper() == f"{tenor} YR"
    }
    if len(recognized) != len(REAL_YEARS):
        raise ValueError("Treasury real-yield CSV missing maturities")
    rows = []
    for record in reader:
        if not record.get("Date"):
            continue
        day = _day(record["Date"])
        if not start <= day <= end:
            continue
        for col, sid in recognized.items():
            raw = (record.get(col) or "").strip()
            value = _clean(raw, sid)
            if value is not None:
                rows.append(QuoteableRateObservation(sid, day, value))
    return _unique(rows)


def parse_bill_csv(content: str, start: date, end: date) -> list[QuoteableRateObservation]:
    reader = csv.reader(StringIO(content.lstrip("\ufeff")))
    header = next(reader, [])
    if not header or header[0].strip().upper() != "DATE":
        raise ValueError("Treasury bill CSV missing Date")
    # The Treasury uses both tenor-qualified and repeated generic coupon
    # headers across exports. Resolve explicit tenors first; associate a
    # generic coupon only with the preceding discount maturity, provided no
    # other maturity intervenes. Never guess from absolute column positions.
    discount: dict[int, int] = {}
    coupon: dict[int, int] = {}
    pending: int | None = None
    for position, cell in enumerate(header[1:], start=1):
        name = re.sub(r"\s+", " ", cell.strip().upper())
        bank = re.fullmatch(r"(\d+) WEEKS? BANK DISCOUNT", name)
        qualified_coupon = re.fullmatch(r"(\d+) WEEKS? COUPON EQUIVALENT", name)
        if bank:
            weeks = int(bank.group(1))
            if weeks not in BILL_WEEKS or weeks in discount:
                raise ValueError(f"Unknown/duplicate Treasury bill discount tenor: {weeks}W")
            discount[weeks] = position
            pending = weeks
        elif qualified_coupon:
            weeks = int(qualified_coupon.group(1))
            if weeks not in BILL_WEEKS or weeks in coupon:
                raise ValueError(f"Unknown/duplicate Treasury bill coupon tenor: {weeks}W")
            coupon[weeks] = position
            pending = None
        elif name == "COUPON EQUIVALENT":
            if pending is None or pending in coupon:
                raise ValueError("Ambiguous Treasury bill coupon equivalent column")
            coupon[pending] = position
            pending = None
        elif "BANK DISCOUNT" in name or "COUPON EQUIVALENT" in name:
            raise ValueError(f"Unrecognized Treasury bill header {name!r}")
        # Unrelated fields are permissible, even between paired columns.
        # A new discount tenor replaces the pending association above.
    if set(discount) != set(BILL_WEEKS):
        raise ValueError("Treasury bill CSV missing week maturities")
    if set(coupon) != set(BILL_WEEKS):
        raise ValueError("Treasury bill CSV missing coupon equivalent columns")
    mapping = {weeks: (discount[weeks], coupon[weeks]) for weeks in BILL_WEEKS}
    rows = []
    for record in reader:
        if not record or not record[0].strip():
            continue
        if len(record) != len(header):
            raise ValueError("Treasury bill CSV malformed row")
        day = _day(record[0])
        if not start <= day <= end:
            continue
        for weeks, (bank_col, coupon_col) in mapping.items():
            for suffix, position in (("DISCOUNT", bank_col), ("COUPON", coupon_col)):
                sid = f"UST_BILL_{suffix}_USD_{weeks}W"
                value = _clean(record[position], sid)
                if value is not None:
                    rows.append(QuoteableRateObservation(sid, day, value))
    return _unique(rows)


class TreasuryRealYieldProvider:
    source = SOURCE_REAL

    def history(self, session: Session, start: date, end: date) -> list[QuoteableRateObservation]:
        rows = []
        for year in range(start.year, end.year + 1):
            response = session.get(
                URL.format(year=year),
                params={
                    "type": "daily_treasury_real_yield_curve",
                    "field_tdr_date_value": str(year),
                    "_format": "csv",
                },
                timeout=45,
            )
            response.raise_for_status()
            rows.extend(parse_real_csv(response.text, start, end))
        return _unique(rows)


class TreasuryBillYieldProvider:
    source = SOURCE_BILL

    def history(self, session: Session, start: date, end: date) -> list[QuoteableRateObservation]:
        rows = []
        for year in range(start.year, end.year + 1):
            response = session.get(
                URL.format(year=year),
                params={
                    "type": "daily_treasury_bill_rates",
                    "field_tdr_date_value": str(year),
                    "_format": "csv",
                },
                timeout=45,
            )
            response.raise_for_status()
            rows.extend(parse_bill_csv(response.text, start, end))
        return _unique(rows)
