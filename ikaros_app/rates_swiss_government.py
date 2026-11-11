"""SNB fitted Swiss Confederation zero-coupon spot yields (monthly observations).

Official cube: rendoblim, monthly period in YYYY-MM, yields in percent.
Do not interpolate monthly values or confuse these yields with policy rates.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date
from io import StringIO
import re

from ikaros_app.rates import RateObservation, RateSeries, _unique, percent_to_decimal

SOURCE = "SNB_GOV"
CUBE = "rendoblim"
URL = f"https://data.snb.ch/api/cube/{CUBE}/data/csv/en"
TENORS = ("1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y", "10Y", "15Y", "20Y", "30Y")
CODES = {f"{int(t[:-1])}J": t for t in TENORS}


def configured_series() -> dict[str, RateSeries]:
    return {
        f"SNB_GOV_SPOT_CHF_{tenor}_MONTHLY": RateSeries(
            series_id=f"SNB_GOV_SPOT_CHF_{tenor}_MONTHLY",
            source=SOURCE,
            currency="CHF",
            curve_type="GOV_ZERO_SPOT",
            tenor=tenor,
            source_key=f"{CUBE}:D0={year_code}",
            compounding="PUBLISHED_SPOT",
            day_count=None,
        )
        for year_code, tenor in CODES.items()
    }


def parse_snb_government_csv(content: str, start: date, end: date) -> list[RateObservation]:
    """Parse the SNB semicolon CSV, including its metadata preamble and quoted header.

    Period dates are normalized to calendar month-end. Missing values aren't
    imputed; negative yields are valid. Refuse unexpected maturities.
    """
    if start > end:
        raise ValueError("Start date after end date")
    lines = content.lstrip("\ufeff").splitlines()
    header_at = None
    for idx, line in enumerate(lines):
        fields = [v.strip().lstrip("\ufeff") for v in next(csv.reader([line], delimiter=";"), [])]
        if (
            fields[:1] == ["Date"]
            and "Value" in fields
            and ("D0" in fields or "Dimension" in fields)
        ):
            header_at = idx
            break
    if header_at is None:
        raise ValueError(f"SNB government response lacks Date/D0/Value header: {lines[:3]!r}")
    reader = csv.DictReader(StringIO("\n".join(lines[header_at:])), delimiter=";")
    results = []
    for row in reader:
        if row.get(None):
            raise ValueError("Unexpected SNB government CSV fields")
        key = (row.get("D0") or row.get("Dimension") or "").strip()
        if key not in CODES:
            raise ValueError(f"Unrecognized SNB government tenor {key!r}")
        period = (row.get("Date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ValueError(f"Unexpected monthly SNB period: {period!r}")
        year, month = map(int, period.split("-"))
        day = date(year, month, calendar.monthrange(year, month)[1])
        if not start <= day <= end:
            continue
        raw = (row.get("Value") or "").strip()
        if raw in ("", ":"):
            continue
        results.append(
            RateObservation(
                f"SNB_GOV_SPOT_CHF_{CODES[key]}_MONTHLY",
                day,
                percent_to_decimal(raw, f"SNB {key}"),
            )
        )
    return _unique(results)


class SNBGovernmentYieldProvider:
    source = SOURCE

    def history(self, session, start: date, end: date) -> list[RateObservation]:
        response = session.get(
            URL,
            params={
                "dimSel": "D0(" + ",".join(CODES) + ")",
                "fromDate": start.strftime("%Y-%m"),
                "toDate": end.strftime("%Y-%m"),
            },
            timeout=60,
            headers={"Accept": "text/csv"},
        )
        response.raise_for_status()
        records = parse_snb_government_csv(response.text, start, end)
        if not records:
            raise ValueError("SNB government feed returned no yields in requested interval")
        return records
