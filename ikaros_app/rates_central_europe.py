"""Official NBP/CNB/MNB policy-rate histories, expanded to effective daily states.

Every daily value is the central-bank policy rate in force on that date, *not*
a new decision or daily traded yield. No interpolation across unobserved history.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET

import requests

from ikaros_app.rates import RateObservation, RateSeries, _unique, percent_to_decimal

NBP_URL = "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"
CNB_URLS = {
    "REPO_2W": "https://www.cnb.cz/en/faq/.galleries/development_of_the_cnb_2w_repo_rate.txt",
    "DISCOUNT": "https://www.cnb.cz/en/faq/.galleries/development_of_the_cnb_discount_rate.txt",
    "LOMBARD": "https://www.cnb.cz/en/faq/.galleries/development_of_the_cnb_lombard_rate.txt",
}
MNB_URL = "https://www.mnb.hu/en/Jegybanki_alapkamat_alakulasa"
NBP_KEYS = {
    "ref": "REFERENCE",
    "dep": "DEPOSIT",
    "lom": "LOMBARD",
    "red": "REDISCOUNT",
    "dys": "DISCOUNT",
}


def configured_series() -> dict[str, RateSeries]:
    result = {}
    for key, name in NBP_KEYS.items():
        sid = f"NBP_{name}_PLN"
        result[sid] = RateSeries(
            sid, "NBP", "PLN", "POLICY_RATE", "ON", key, "PUBLISHED_POLICY_RATE", None
        )
    for name in CNB_URLS:
        sid = f"CNB_{name}_CZK"
        result[sid] = RateSeries(
            sid,
            "CNB",
            "CZK",
            "POLICY_RATE",
            "2W" if name == "REPO_2W" else "ON",
            name,
            "PUBLISHED_POLICY_RATE",
            None,
        )
    sid = "MNB_BASE_HUF"
    result[sid] = RateSeries(
        sid, "MNB", "HUF", "POLICY_RATE", "ON", "BASE", "PUBLISHED_POLICY_RATE", None
    )
    return result


def _rate(value: str, label: str) -> float:
    return percent_to_decimal(value.strip().replace("%", "").replace(",", "."), label)


def _effective_daily(
    events: list[tuple[date, float]], sid: str, start: date, end: date
) -> list[RateObservation]:
    """Carry verified policy rates forward from their effective date, including weekends."""
    if start > end:
        raise ValueError("start after end")
    observed: dict[date, float] = {}
    for day, value in events:
        if day in observed and observed[day] != value:
            raise ValueError(f"Conflicting {sid} rate on {day}")
        observed[day] = value
    if not observed:
        raise ValueError(f"No published history for {sid}")
    preceding = [day for day in observed if day <= start]
    if not preceding:
        raise ValueError(f"No historical baseline rate for {sid} at {start}")
    current = observed[max(preceding)]
    result = []
    day = start
    while day <= end:
        current = observed.get(day, current)
        result.append(RateObservation(sid, day, current))
        day += timedelta(days=1)
    return result


def parse_nbp_xml(content: bytes | str, start: date, end: date) -> list[RateObservation]:
    """Parse the NBP XML document using its declared character encoding.

    Always prefer HTTP response.content (bytes) over response.text: the HTTP
    charset may differ from the encoding declared within NBP's XML archive.
    """
    if isinstance(content, bytes):
        payload = content
        if not payload.strip():
            raise ValueError("Empty NBP XML response")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            preview = payload[:120].lstrip().lower()
            if b"<html" in preview or b"<!doctype html" in preview:
                raise ValueError("NBP endpoint returned HTML instead of XML") from exc
            raise ValueError("Invalid NBP XML response; check source encoding/content") from exc
    elif isinstance(content, str):
        # String input remains supported for offline fixtures. HTTP retrieval
        # deliberately uses bytes so XML declarations govern actual decoding.
        try:
            root = ET.fromstring(content.lstrip("\ufeff"))
        except ET.ParseError as exc:
            raise ValueError("Invalid NBP XML response") from exc
    else:
        raise TypeError("NBP XML content must be bytes or str")
    changes = {key: [] for key in NBP_KEYS}
    for block in root.iter():
        raw_day = block.attrib.get("obowiazuje_od")
        if not raw_day:
            continue
        day = date.fromisoformat(raw_day)
        for entry in block.iter():
            key = entry.attrib.get("id")
            if key in changes and "oprocentowanie" in entry.attrib:
                changes[key].append((day, _rate(entry.attrib["oprocentowanie"], key)))
    return _unique(
        [
            obs
            for key, name in NBP_KEYS.items()
            for obs in _effective_daily(changes[key], f"NBP_{name}_PLN", start, end)
        ]
    )


def parse_cnb_txt(content: str, rate_name: str, start: date, end: date) -> list[RateObservation]:
    if rate_name not in CNB_URLS:
        raise ValueError(f"Unknown CNB policy rate: {rate_name}")
    lines = [line.strip().lstrip("\ufeff") for line in content.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("VALID_FROM|CNB_"):
        raise ValueError("Unexpected CNB history header")
    events = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) != 2 or not re.fullmatch(r"\d{8}", parts[0]):
            raise ValueError(f"Invalid CNB history row {line!r}")
        events.append((datetime.strptime(parts[0], "%Y%m%d").date(), _rate(parts[1], rate_name)))
    return _effective_daily(events, f"CNB_{rate_name}_CZK", start, end)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cell = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag: str, attrs: list):
        if tag == "tr":
            self.in_row, self.row = True, []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell, self.cell = True, []

    def handle_data(self, data: str):
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str):
        if tag in ("td", "th") and self.in_cell:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False


def parse_mnb_html(content: str, start: date, end: date) -> list[RateObservation]:
    parser = _TableParser()
    parser.feed(content)
    events = []
    for cells in parser.rows:
        if len(cells) < 2:
            continue
        raw_date = cells[0].strip()
        raw_rate = cells[1].strip()
        if not re.fullmatch(pattern=r"\d{1,2} [A-Za-z]+ \d{4}", string=raw_date):
            continue
        if not re.fullmatch(pattern=r"-?\d+[.,]\d+\s*%", string=raw_rate):
            raise ValueError(f"Invalid MNB policy rate: {raw_date} {raw_rate!r}")
        events.append(
            (datetime.strptime(raw_date, "%d %B %Y").date(), _rate(value=raw_rate, label="MNB"))
        )
    return _effective_daily(events, "MNB_BASE_HUF", start, end)


class NBPPolicyProvider:
    source = "NBP"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        response = session.get(NBP_URL, timeout=45)
        response.raise_for_status()
        return parse_nbp_xml(response.content, start, end)


class CNBPolicyProvider:
    source = "CNB"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        records = []
        for name, url in CNB_URLS.items():
            response = session.get(url, timeout=45)
            response.raise_for_status()
            records.extend(parse_cnb_txt(response.text, name, start, end))
        return _unique(records)


class MNBPolicyProvider:
    source = "MNB"

    def history(self, session: requests.Session, start: date, end: date) -> list[RateObservation]:
        # Look farther back for the rate in force on the requested start date.
        response = session.get(
            MNB_URL,
            params={"datefrom": "01/01/1987", "datetill": end.strftime("%m/%d/%Y"), "order": "0"},
            timeout=60,
        )
        response.raise_for_status()
        return parse_mnb_html(response.text, start, end)
