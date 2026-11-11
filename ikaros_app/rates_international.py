"""Official GBP, EUR overnight, and CHF monthly policy-rate source adapters.

Append-only additions to ikaros_app.rates definitions; no new persistence layer.
Never invent missing sessions or interpolate published observations.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta
from io import StringIO
import re
import time

import requests

from ikaros_app.rates import RateObservation, RateSeries, _unique, percent_to_decimal

BOE_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
ECB_EST_URL = "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT"
SNB_URL = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
BOE_SERIES = {"IUDBEDR": "BOE_BANK_RATE_GBP", "IUDSOIA": "BOE_SONIA_GBP_ON"}


def configured_series() -> dict[str, RateSeries]:
    return {
        "BOE_BANK_RATE_GBP": RateSeries(
            "BOE_BANK_RATE_GBP",
            "BOE",
            "GBP",
            "POLICY_RATE",
            "ON",
            "IUDBEDR",
            "PUBLISHED_POLICY_RATE",
            None,
        ),
        "BOE_SONIA_GBP_ON": RateSeries(
            "BOE_SONIA_GBP_ON",
            "BOE",
            "GBP",
            "OVERNIGHT_REFERENCE",
            "ON",
            "IUDSOIA",
            "PUBLISHED_OVERNIGHT",
            None,
        ),
        "ECB_ESTR_EUR_ON": RateSeries(
            "ECB_ESTR_EUR_ON",
            "ECB_EST",
            "EUR",
            "OVERNIGHT_REFERENCE",
            "ON",
            "EST.B.EU000A2X2A25.WT",
            "PUBLISHED_OVERNIGHT",
            None,
        ),
        "SNB_POLICY_CHF_MONTHLY": RateSeries(
            "SNB_POLICY_CHF_MONTHLY",
            "SNB",
            "CHF",
            "POLICY_RATE_MONTH_END",
            "1M",
            "EPB@SNB.snboffzisa{LZ}",
            "PUBLISHED_MONTH_END",
            None,
        ),
    }


def _parse_day(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%d %b %Y", "%d %b %y", "%d/%b/%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized official rates date {raw!r}")


def parse_boe_csv(content: str, start: date, end: date) -> list[RateObservation]:
    rows = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    columns = {c.strip().upper(): c for c in rows.fieldnames or ()}
    if "DATE" not in columns or not set(BOE_SERIES).intersection(columns):
        raise ValueError(f"BoE response missing expected columns: {list(columns)}")
    out = []
    for row in rows:
        raw_day = (row.get(columns["DATE"]) or "").strip()
        if not raw_day:
            continue
        day = _parse_day(raw_day)
        if not start <= day <= end:
            continue
        for code, sid in BOE_SERIES.items():
            if code not in columns:
                continue
            raw = (row.get(columns[code]) or "").strip()
            if not raw or raw.lower() in {"n/a", "na", "-", ".."}:
                continue
            out.append(RateObservation(sid, day, percent_to_decimal(raw, code)))
    return _unique(out)


def parse_estr_csv(content: str, start: date, end: date) -> list[RateObservation]:
    rows = csv.DictReader(StringIO(content.lstrip("\ufeff")))
    if not rows.fieldnames or not {"TIME_PERIOD", "OBS_VALUE"} <= set(rows.fieldnames):
        raise ValueError("ECB €STR response missing TIME_PERIOD/OBS_VALUE")
    out = []
    for row in rows:
        value = (row.get("OBS_VALUE") or "").strip()
        if not value:
            continue
        day = date.fromisoformat(row["TIME_PERIOD"].strip())
        if not start <= day <= end:
            continue
        # The request is to one exact official series; verify key if supplied.
        key = (row.get("KEY") or row.get("SERIES_KEY") or "").strip()
        if key and not key.endswith("EST.B.EU000A2X2A25.WT"):
            raise ValueError(f"Unexpected ECB €STR key: {key}")
        out.append(RateObservation("ECB_ESTR_EUR_ON", day, percent_to_decimal(value, "€STR")))
    return _unique(out)


def parse_snb_csv(content: str, start: date, end: date) -> list[RateObservation]:
    """Parse official SNB semicolon CSV (metadata preamble and quoted headers).

    Select dimension D0=LZ only. Monthly periods are represented by month-end
    dates; they are not policy-decision effective dates.
    """
    lines = content.lstrip("\ufeff").splitlines()
    header_index = None
    field_names = None
    for index, line in enumerate(lines):
        # Use the CSV parser rather than a regex: SNB quotes its header fields.
        parsed = next(csv.reader([line], delimiter=";"), [])
        fields = [item.strip().lstrip("\ufeff") for item in parsed]
        if len(fields) >= 3 and fields[0].lower() == "date" and fields[-1].lower() == "value":
            if "D0" not in fields and "Dimension" not in fields:
                continue
            header_index, field_names = index, fields
            break
    if header_index is None or field_names is None:
        preview = " | ".join(lines[:5])[:300]
        raise ValueError(f"SNB response missing Date/D0/Value CSV header; preview={preview!r}")
    reader = csv.DictReader(
        StringIO("\n".join(lines[header_index + 1 :])),
        fieldnames=field_names,
        delimiter=";",
    )
    out = []
    for row in reader:
        if row.get(None):
            raise ValueError("Unexpected extra SNB CSV fields")
        dim = (row.get("D0") or row.get("Dimension") or "").strip()
        if dim != "LZ":
            continue
        period = (row.get("Date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ValueError(f"Invalid SNB monthly period: {period!r}")
        year, month = map(int, period.split("-"))
        day = date(year, month, calendar.monthrange(year, month)[1])
        if not start <= day <= end:
            continue
        raw = (row.get("Value") or "").strip()
        if raw in {"", ":"}:
            continue
        out.append(
            RateObservation(
                "SNB_POLICY_CHF_MONTHLY",
                day,
                percent_to_decimal(raw, "SNB policy"),
            )
        )
    return _unique(out)


class BoERatesProvider:
    source = "BOE"

    def history(self, session, start: date, end: date) -> list[RateObservation]:
        response = session.get(
            BOE_URL,
            params={
                "csv.x": "yes",
                "Datefrom": start.strftime("%d/%b/%Y"),
                "Dateto": end.strftime("%d/%b/%Y"),
                "SeriesCodes": ",".join(BOE_SERIES),
                "CSVF": "TN",
                "UsingCodes": "Y",
                "VPD": "Y",
                "VFD": "N",
            },
            timeout=60,
            headers={"User-Agent": "IkarosMarketResearch/1.0 (research; official-rate-history)"},
        )
        response.raise_for_status()
        return parse_boe_csv(response.text, start, end)


class ECBESTRProvider:
    """Official ECB €STR, with bounded transient-error retries and window fallback.

    A long full-period request may time out at the ECB gateway. On 502/503/504
    or connection timeouts, retry briefly, then split long periods into 60-day
    windows. Never silently treat a failed window as an empty observation set.
    """

    source = "ECB_EST"
    _TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
    _MAX_ATTEMPTS = 3
    _WINDOW_DAYS = 60

    @staticmethod
    def _request(session, start: date, end: date) -> list[RateObservation]:
        last_error = None
        for attempt in range(ECBESTRProvider._MAX_ATTEMPTS):
            try:
                response = session.get(
                    ECB_EST_URL,
                    params={
                        "format": "csvdata",
                        "startPeriod": start.isoformat(),
                        "endPeriod": end.isoformat(),
                    },
                    timeout=(10, 30),
                    headers={"Accept": "text/csv"},
                )
                response.raise_for_status()
                return parse_estr_csv(response.text, start, end)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in ECBESTRProvider._TRANSIENT_STATUSES:
                    raise
                last_error = exc
            if attempt + 1 < ECBESTRProvider._MAX_ATTEMPTS:
                time.sleep(0.5 * (2**attempt))
        assert last_error is not None
        raise last_error

    def history(self, session, start: date, end: date) -> list[RateObservation]:
        if end < start:
            raise ValueError("ECB €STR end must not precede start")
        try:
            return self._request(session, start, end)
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            # A short-window failure is not helped by further splitting.
            if (end - start).days + 1 <= self._WINDOW_DAYS:
                raise
            if isinstance(exc, requests.HTTPError):
                status = exc.response.status_code if exc.response is not None else None
                if status not in self._TRANSIENT_STATUSES:
                    raise

        values = []
        begin = start
        while begin <= end:
            finish = min(end, begin + timedelta(days=self._WINDOW_DAYS - 1))
            try:
                values.extend(self._request(session, begin, finish))
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                raise RuntimeError(
                    f"ECB €STR unavailable for {begin}..{finish}; the batch was not persisted"
                ) from exc
            begin = finish + timedelta(days=1)
        return _unique(values)


class SNBPolicyProvider:
    source = "SNB"

    def history(self, session, start: date, end: date) -> list[RateObservation]:
        response = session.get(
            SNB_URL,
            params={
                "dimSel": "D0(LZ)",
                "fromDate": start.strftime("%Y-%m"),
                "toDate": end.strftime("%Y-%m"),
            },
            timeout=60,
        )
        response.raise_for_status()
        return parse_snb_csv(response.text, start, end)
