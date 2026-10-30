"""Typed provider interface and concrete daily market-data adapters.

Adapters contain no Kafka or database logic. Optional OpenBB imports are lazy,
so offline tests and CLI help work without a network or Snowflake connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class PriceHistoryProvider(Protocol):
    """Retrieve historical observations in canonical dataframe form.

    ``end`` is exclusive; returned records contain a ``date`` column.
    Provider exceptions propagate to the operator, never silently become zeros.
    """

    dataset: str
    name: str

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


@dataclass(frozen=True)
class OpenBBFXProvider:
    dataset: str = "FX"
    name: str = "openbb-yfinance-fx"

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from run.ingest_fx_rates import fetch_fx_history

        return fetch_fx_history(symbol, start.isoformat(), end.isoformat())


@dataclass(frozen=True)
class OpenBBEquityProvider:
    dataset: str = "GPW"
    name: str = "openbb-yfinance-equity"

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from ingestion.prices import fetch_eod_prices

        return fetch_eod_prices(
            symbol=symbol, start_date=start.isoformat(), end_date=end.isoformat()
        )


class ProviderRegistry:
    """Small, explicit provider registry; no dynamic arbitrary module loading."""

    def __init__(self, providers: tuple[PriceHistoryProvider, ...] | None = None):
        providers = (
            providers if providers is not None else (OpenBBFXProvider(), OpenBBEquityProvider())
        )
        self._providers: dict[str, PriceHistoryProvider] = {}
        for provider in providers:
            dataset = provider.dataset.upper()
            if dataset in self._providers:
                raise ValueError(f"Duplicate provider for dataset {dataset}")
            self._providers[dataset] = provider

    def get(self, dataset: str) -> PriceHistoryProvider:
        key = dataset.upper()
        try:
            return self._providers[key]
        except KeyError as exc:
            raise ValueError(f"No daily price provider for {key}") from exc

    def describe(self) -> dict[str, str]:
        return {k: v.name for k, v in sorted(self._providers.items())}
