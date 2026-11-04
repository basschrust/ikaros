"""Additional derivative listing parsers. Discovery is NOT contract registration.

Parse provider-reported symbols/expiry only; strike, multiplier, contract type
and exercise style must be verified independently before registration.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from bs4 import BeautifulSoup

from ingestion.contract_discovery import Candidate

OPTION = re.compile(r"^OW20[A-Z0-9]{5,16}$")
DATE_ISO = re.compile(r"\b20\d\d-\d\d-\d\d\b")
DATE_DMY = re.compile(r"\b\d\d[./]\d\d[./]20\d\d\b")


def _expiry(text: str) -> str | None:
    iso = DATE_ISO.search(text)
    if iso:
        return date.fromisoformat(iso.group()).isoformat()
    dmy = DATE_DMY.search(text)
    if dmy:
        return datetime.strptime(dmy.group().replace('/', '.'), '%d.%m.%Y').date().isoformat()
    return None


def parse_bankier_options(html: str, *, as_of: str | None = None) -> list[Candidate]:
    """Read WIG20 options from tables that publish symbol and exercise date.

    A row with no verifiable expiry is still a discovery candidate, but cannot
    be promoted automatically. Never decode strike or call/put from symbol here.
    """
    soup = BeautifulSoup(html, 'html.parser')
    candidates: dict[str, Candidate] = {}
    for table in soup.find_all('table'):
        trs = table.find_all('tr')
        for header_position, tr in enumerate(trs):
            headers = [th.get_text(' ', strip=True).lower() for th in tr.find_all('th')]
            if not headers:
                continue
            expiry_cols = [i for i, h in enumerate(headers) if 'wykonan' in h or 'wygas' in h]
            if not expiry_cols:
                continue
            expiry_idx = expiry_cols[-1]
            symbol_idx = next((i for i, h in enumerate(headers) if 'walor' in h or 'symbol' in h), 0)
            for row in trs[header_position + 1:]:
                cells = row.find_all('td')
                if len(cells) <= max(symbol_idx, expiry_idx):
                    continue
                raw = cells[symbol_idx].get_text(' ', strip=True)
                symbol = raw.split()[0].upper() if raw else ''
                if not OPTION.fullmatch(symbol):
                    continue
                expiry = _expiry(cells[expiry_idx].get_text(' ', strip=True))
                candidates[symbol] = Candidate(symbol, 'OPTION', expiry, 'bankier', as_of)
            break
    return sorted(candidates.values(), key=lambda c: c.symbol)
