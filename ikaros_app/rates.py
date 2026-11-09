"""Official sovereign yield observations. Never treat yields as traded OHLC prices.

Values are stored as decimals (4.25 percent -> 0.0425). A Treasury
par-yield observation is not interchangeable with an ECB spot yield.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
import csv
import math
import re

import requests

UST_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/YC/{key}"
UST_TENORS = {
    "1 Mo": "1M", "1.5 Mo": "1_5M", "2 Mo": "2M", "3 Mo": "3M",
    "4 Mo": "4M", "6 Mo": "6M", "1 Yr": "1Y", "2 Yr": "2Y",
    "3 Yr": "3Y", "5 Yr": "5Y", "7 Yr": "7Y", "10 Yr": "10Y",
    "20 Yr": "20Y", "30 Yr": "30Y",
}
ECB_TENORS = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
RATE_SOURCES = ("UST", "ECB", "NYFED")
NYFED_ENDPOINTS = {
    "SOFR": "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json",
    "EFFR": "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json",
}


@dataclass(frozen=True)
class RateSeries:
    series_id: str
    source: str
    currency: str
    curve_type: str
    tenor: str
    source_key: str
    compounding: str
    day_count: str | None


@dataclass(frozen=True)
class RateObservation:
    series_id: str
    day: date
    rate: float


def configured_series() -> dict[str, RateSeries]:
    series = {}
    for tenor in UST_TENORS.values():
        sid = f"UST_PAR_USD_{tenor}"
        series[sid] = RateSeries(sid, "UST", "USD", "GOV_PAR", tenor,
                                 "daily_treasury_yield_curve", "PUBLISHED_PAR_YIELD", None)
    for tenor in ECB_TENORS:
        sid = f"ECB_AAA_SPOT_EUR_{tenor}"
        key = f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}"
        series[sid] = RateSeries(sid, "ECB", "EUR", "GOV_AAA_SPOT", tenor,
                                 key, "CONTINUOUS", None)
    for rate_name in NYFED_ENDPOINTS:
        sid = f"NYFED_{rate_name}_USD_ON"
        series[sid] = RateSeries(
            sid, "NYFED", "USD", "OVERNIGHT_REFERENCE", "ON",
            rate_name, "PUBLISHED_OVERNIGHT", None,
        )
    return series


def percent_to_decimal(value: str, label: str) -> float:
    result = float(value) / 100.0
    if not math.isfinite(result) or not -1 < result < 10:
        raise ValueError(f"Invalid rate {label}: {value!r}")
    return result


def _unique(records: list[RateObservation]) -> list[RateObservation]:
    seen: dict[tuple[str, date], float] = {}
    for r in records:
        key = (r.series_id, r.day)
        if key in seen and seen[key] != r.rate:
            raise ValueError(f"Conflicting rate observation {key}")
        seen[key] = r.rate
    return [RateObservation(s, d, v) for (s, d), v in sorted(seen.items())]


def parse_treasury_csv(content: str, start: date, end: date) -> list[RateObservation]:
    rows = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    if not rows.fieldnames or "Date" not in rows.fieldnames:
        raise ValueError("Treasury response missing Date column")
    recognized = {column: tenor for column, tenor in UST_TENORS.items()
                  if column in rows.fieldnames}
    if not recognized:
        raise ValueError("Treasury response has no recognized maturity columns")
    result = []
    for row in rows:
        if not row.get("Date"):
            continue
        stamp = row["Date"].strip()
        day = (date.fromisoformat(stamp) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp)
               else datetime.strptime(stamp, "%m/%d/%Y").date())
        if start <= day <= end:
            for column, tenor in recognized.items():
                raw = (row.get(column) or "").strip()
                if raw and raw.upper() not in {"N/A", "ND"}:
                    result.append(RateObservation(f"UST_PAR_USD_{tenor}", day,
                                                  percent_to_decimal(raw, column)))
    return _unique(result)


def parse_ecb_csv(content: str, start: date, end: date) -> list[RateObservation]:
    rows = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    if not rows.fieldnames or not {"TIME_PERIOD", "OBS_VALUE"}.issubset(rows.fieldnames):
        raise ValueError("ECB response missing TIME_PERIOD/OBS_VALUE")
    result = []
    for row in rows:
        raw = (row.get("OBS_VALUE") or "").strip()
        if not raw:
            continue
        day = date.fromisoformat(row["TIME_PERIOD"])
        if not start <= day <= end:
            continue
        # YC SDMX-CSV identifies the last dimension as DATA_TYPE_FM.
        kind = (row.get("DATA_TYPE_FM") or row.get("DATA_TYPE") or "").strip()
        if not kind:
            key = row.get("KEY") or row.get("SERIES_KEY") or ""
            kind = key.split(".")[-1]
        match = re.fullmatch(r"SR_(\d+[MY])", kind)
        if match is None:
            raise ValueError(f"Unknown ECB spot-rate series: {kind!r}")
        tenor = match.group(1)
        if tenor not in ECB_TENORS:
            raise ValueError(f"Unexpected ECB tenor {tenor}")
        result.append(RateObservation(f"ECB_AAA_SPOT_EUR_{tenor}", day,
                                      percent_to_decimal(raw, kind)))
    return _unique(result)


class TreasuryYieldProvider:
    """Official US Treasury CSV, downloaded once for each calendar year."""

    source = "UST"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        records = []
        for year in range(start.year, end.year + 1):
            r = session.get(
                UST_URL.format(year=year),
                params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": str(year),
                        "_format": "csv"}, timeout=45,
            )
            r.raise_for_status()
            records.extend(parse_treasury_csv(r.text, start, end))
        return _unique(records)


class ECBSpotYieldProvider:
    """ECB euro-area AAA sovereign zero-coupon spot yields (Svensson fit)."""

    source = "ECB"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        key = "B.U2.EUR.4F.G_N_A.SV_C_YM." + "+".join(f"SR_{t}" for t in ECB_TENORS)
        r = session.get(
            ECB_URL.format(key=key),
            params={"format": "csvdata", "startPeriod": start.isoformat(),
                    "endPeriod": end.isoformat()}, timeout=60,
            headers={"Accept": "text/csv"},
        )
        r.raise_for_status()
        return parse_ecb_csv(r.text, start, end)


def parse_nyfed_json(
    payload: dict, rate_name: str, start: date, end: date
) -> list[RateObservation]:
    """Read official NY Fed reference-rate records, never infer missing days."""
    if rate_name not in NYFED_ENDPOINTS:
        raise ValueError(f"Unsupported NY Fed reference rate: {rate_name}")
    if not isinstance(payload, dict) or not isinstance(payload.get("refRates"), list):
        raise ValueError("NY Fed response missing refRates list")
    records = []
    for item in payload["refRates"]:
        if not isinstance(item, dict) or item.get("type") != rate_name:
            raise ValueError(f"Unexpected NY Fed rate type in {rate_name} response")
        day = date.fromisoformat(item["effectiveDate"])
        if start <= day <= end:
            value = item.get("percentRate")
            if value is None:
                raise ValueError(f"Missing {rate_name} rate for {day}")
            records.append(RateObservation(
                f"NYFED_{rate_name}_USD_ON", day,
                percent_to_decimal(str(value), rate_name),
            ))
    return _unique(records)


class NYFedOvernightProvider:
    """Official SOFR and EFFR reference rates; effective dates, not publication dates."""

    source = "NYFED"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        records = []
        for rate_name, url in NYFED_ENDPOINTS.items():
            response = session.get(
                url,
                params={
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "type": "rate",
                },
                timeout=45,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            records.extend(parse_nyfed_json(response.json(), rate_name, start, end))
        return _unique(records)


def selected_sources(series_ids: list[str] | None) -> tuple[dict[str, RateSeries], set[str]]:
    all_series = configured_series()
    wanted = set(series_ids or all_series)
    unknown = wanted - set(all_series)
    if unknown:
        raise ValueError(f"Unknown rate series: {', '.join(sorted(unknown))}")
    return {s: all_series[s] for s in sorted(wanted)}, {all_series[s].source for s in wanted}
