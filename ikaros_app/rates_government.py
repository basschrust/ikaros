"""Official government yield observations; not bootstrapped discount curves.

BoE: daily nominal gilt par and zero-coupon fitted yields, 5/10/20Y.
Eurostat: monthly EMU-convergence 10Y benchmark government yields, PL/CZ/HU.
No interpolation or synthetic daily observations; rates stored in decimal units.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime
from io import StringIO
from math import prod
from requests import Session

from ikaros_app.rates import RateObservation, RateSeries, _unique, percent_to_decimal

BOE_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/irt_lt_mcby_m"
BOE_CODES = {
    "IUDSNPY": ("BOE_GILT_PAR_GBP_5Y", "GOV_PAR", "5Y", "PUBLISHED_PAR_YIELD"),
    "IUDMNPY": ("BOE_GILT_PAR_GBP_10Y", "GOV_PAR", "10Y", "PUBLISHED_PAR_YIELD"),
    "IUDLNPY": ("BOE_GILT_PAR_GBP_20Y", "GOV_PAR", "20Y", "PUBLISHED_PAR_YIELD"),
    "IUDSNZC": ("BOE_GILT_SPOT_GBP_5Y", "GOV_SPOT_FITTED", "5Y", "PUBLISHED_ZERO_COUPON"),
    "IUDMNZC": ("BOE_GILT_SPOT_GBP_10Y", "GOV_SPOT_FITTED", "10Y", "PUBLISHED_ZERO_COUPON"),
    "IUDLNZC": ("BOE_GILT_SPOT_GBP_20Y", "GOV_SPOT_FITTED", "20Y", "PUBLISHED_ZERO_COUPON"),
}
EUROSTAT_GEO = {"PL": "PLN", "CZ": "CZK", "HU": "HUF"}


def configured_series() -> dict[str, RateSeries]:
    result = {}
    for code, (sid, curve_type, tenor, compounding) in BOE_CODES.items():
        result[sid] = RateSeries(sid, "BOE_GILT", "GBP", curve_type, tenor, code, compounding, None)
    for geo, ccy in EUROSTAT_GEO.items():
        sid = f"EUROSTAT_GOV10Y_{ccy}_MONTHLY"
        result[sid] = RateSeries(
            sid,
            "EUROSTAT_GOV",
            ccy,
            "GOV_BENCHMARK_10Y_MONTHLY",
            "10Y",
            f"irt_lt_mcby_m:{geo}",
            "PUBLISHED_BENCHMARK_YIELD",
            None,
        )
    return result


def _boe_day(raw: str) -> date:
    for fmt in ("%d %b %Y", "%d %b %y", "%d/%b/%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized BoE date {raw!r}")


def parse_boe_gilt_csv(content: str, start: date, end: date) -> list[RateObservation]:
    reader = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    headers = {h.strip().upper(): h for h in (reader.fieldnames or ()) if h}
    if "DATE" not in headers or not set(BOE_CODES).issubset(headers):
        raise ValueError(f"BoE gilt CSV missing columns: {sorted(set(BOE_CODES) - set(headers))}")
    observations = []
    for row in reader:
        stamp = (row.get(headers["DATE"]) or "").strip()
        if not stamp:
            continue
        day = _boe_day(stamp)
        if not start <= day <= end:
            continue
        for code, (sid, _, _, _) in BOE_CODES.items():
            raw = (row.get(headers[code]) or "").strip()
            if raw and raw.lower() not in {"n/a", "na", "-", ".."}:
                observations.append(RateObservation(sid, day, percent_to_decimal(raw, code)))
    return _unique(observations)


def _label_position(dim: dict, label: str) -> int:
    mapping = dim["category"]["index"]
    if isinstance(mapping, dict):
        if label not in mapping:
            raise ValueError(f"Eurostat dimension missing {label}")
        return int(mapping[label])
    ids = dim["category"].get("label", {})
    raise ValueError(
        f"Unsupported Eurostat index representation: {type(mapping)!r}, {list(ids)[:3]}"
    )


def parse_eurostat_json(payload: dict, geo: str, start: date, end: date) -> list[RateObservation]:
    """Decode JSON-stat flat indices; reject mixed geographies or unknown dimensions."""
    if geo not in EUROSTAT_GEO:
        raise ValueError(f"Unsupported Eurostat geography {geo}")
    ids = payload.get("id")
    sizes = payload.get("size")
    dims = payload.get("dimension", {})
    values = payload.get("value")
    if not isinstance(ids, list) or not isinstance(sizes, list) or len(ids) != len(sizes):
        raise ValueError("Eurostat response missing JSON-stat dimensions")
    if not isinstance(values, dict) or "geo" not in ids or "time" not in ids:
        raise ValueError("Eurostat response missing geography, time or data")
    if any(not isinstance(n, int) or n < 1 for n in sizes):
        raise ValueError("Eurostat invalid dimension sizes")
    if prod(sizes) > 200000:
        raise ValueError("Eurostat unexpectedly large response")
    geo_idx = _label_position(dims["geo"], geo)
    times = dims["time"]["category"]["index"]
    if not isinstance(times, dict):
        raise ValueError("Eurostat response has unsupported time index")
    time_at = {int(pos): stamp for stamp, pos in times.items()}
    if "freq" in ids:
        _label_position(dims["freq"], "M")
    for name, size in zip(ids, sizes):
        if name not in {"geo", "time"} and size != 1:
            raise ValueError(f"Eurostat unexpected multiple {name} values")
    observations = []
    for raw_flat, raw_value in values.items():
        if raw_value is None:
            continue
        flat = int(raw_flat)
        if not 0 <= flat < prod(sizes):
            raise ValueError("Eurostat observation index out of bounds")
        positions = [0] * len(sizes)
        for i in range(len(sizes) - 1, -1, -1):
            positions[i] = flat % sizes[i]
            flat //= sizes[i]
        if positions[ids.index("geo")] != geo_idx:
            raise ValueError("Eurostat response contains unrequested geography")
        stamp = time_at[positions[ids.index("time")]]
        year, month = map(int, stamp.split("-"))
        if month not in range(1, 13):
            raise ValueError(f"Invalid Eurostat month {stamp}")
        day = date(year, month, calendar.monthrange(year, month)[1])
        if start <= day <= end:
            sid = f"EUROSTAT_GOV10Y_{EUROSTAT_GEO[geo]}_MONTHLY"
            observations.append(RateObservation(sid, day, percent_to_decimal(str(raw_value), sid)))
    return _unique(observations)


class BoEGiltYieldProvider:
    source = "BOE_GILT"

    def history(self, session: Session, start: date, end: date) -> list[RateObservation]:
        response = session.get(
            BOE_URL,
            params={
                "csv.x": "yes",
                "Datefrom": start.strftime("%d/%b/%Y"),
                "Dateto": end.strftime("%d/%b/%Y"),
                "SeriesCodes": ",".join(BOE_CODES),
                "CSVF": "TN",
                "UsingCodes": "Y",
                "VPD": "Y",
                "VFD": "N",
            },
            timeout=60,
        )
        response.raise_for_status()
        return parse_boe_gilt_csv(response.text, start, end)


class EurostatGovYieldProvider:
    source = "EUROSTAT_GOV"

    def history(self, session: Session, start: date, end: date) -> list[RateObservation]:
        result = []
        # A separate country request makes mixed-country responses detectable.
        for geo in EUROSTAT_GEO:
            response = session.get(
                EUROSTAT_URL,
                params={
                    "geo": geo,
                    "sinceTimePeriod": f"{start.year:04d}-{start.month:02d}",
                    "untilTimePeriod": f"{end.year:04d}-{end.month:02d}",
                    "lang": "EN",
                },
                timeout=60,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            result.extend(parse_eurostat_json(response.json(), geo, start, end))
        return _unique(result)
