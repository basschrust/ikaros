"""Derivative listing adapters (candidate discovery, not pricing/registration)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ingestion.contract_discovery import Candidate, parse_bankier_futures, parse_money_options
from ingestion.derivatives_discovery_ext import parse_bankier_options

FUTURES_URL = 'https://www.bankier.pl/gielda/notowania/futures'
OPTIONS_URL = 'https://www.bankier.pl/gielda/notowania/opcje'
MONEY_OPTIONS_URL = 'https://www.money.pl/gielda/gpw/opcje/'


class ListingSource(Protocol):
    name: str
    def discover(self, *, as_of: str) -> list[Candidate]: ...


@dataclass
class HtmlListingSource:
    name: str
    url: str
    parser: object
    snapshot: Path | None = None
    timeout: int = 25

    def discover(self, *, as_of: str) -> list[Candidate]:
        if self.snapshot is not None:
            html = self.snapshot.read_text(encoding='utf-8')
        else:
            import requests
            response = requests.get(self.url, timeout=self.timeout, headers={'User-Agent':'IkarosResearch/1.0'})
            response.raise_for_status()
            html = response.text
        return self.parser(html, as_of=as_of)


def sources(*, futures_html: str | None = None, options_html: str | None = None,
            options_source: str = 'bankier') -> list[ListingSource]:
    """Return explicit candidate sources, preserving local snapshot support."""
    if options_source not in ('bankier', 'money', 'both'):
        raise ValueError(f'Unsupported options source: {options_source}')
    if options_source == 'both' and options_html:
        raise ValueError('A single --options-html snapshot cannot represent both option sources')
    result: list[ListingSource] = [HtmlListingSource(
        'bankier-futures', FUTURES_URL, parse_bankier_futures,
        Path(futures_html) if futures_html else None)]
    if options_source in ('bankier','both'):
        result.append(HtmlListingSource('bankier-options', OPTIONS_URL, parse_bankier_options,
            Path(options_html) if options_html and options_source == 'bankier' else None))
    if options_source in ('money','both'):
        result.append(HtmlListingSource('money-options', MONEY_OPTIONS_URL, parse_money_options,
            Path(options_html) if options_html and options_source == 'money' else None))
    return result


def collect_candidates(adapters: list[ListingSource], *, as_of: str) -> tuple[list[Candidate], dict[str,int]]:
    """Require each selected source to return data; do not misreport empty as delisting."""
    output: dict[tuple[str,str], Candidate] = {}
    counts: dict[str,int] = {}
    for adapter in adapters:
        discovered = adapter.discover(as_of=as_of)
        if not discovered:
            raise RuntimeError(f'{adapter.name}: empty listing; source may have changed')
        counts[adapter.name] = len(discovered)
        for item in discovered:
            key = (item.symbol, item.source)
            existing = output.get(key)
            if existing is not None and existing != item:
                raise ValueError(f'Conflicting candidate for {key}')
            output[key] = item
    return sorted(output.values(), key=lambda c: (c.instrument_type,c.symbol,c.source)), counts
