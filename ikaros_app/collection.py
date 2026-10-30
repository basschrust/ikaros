"""Convert provider dataframes to validated Kafka events, independently of Kafka."""

from __future__ import annotations

from datetime import date, timedelta
import logging
import math

import pandas as pd

from ingestion.kafka_inbound import Observation
from ikaros_app.providers import PriceHistoryProvider, ProviderRegistry

LOG = logging.getLogger(__name__)


def _value(record: dict, field: str) -> float | None:
    item = record.get(field)
    if item is None or pd.isna(item):
        return None
    result = float(item)
    return result if math.isfinite(result) else None


def convert_daily_rows(
    frame: pd.DataFrame,
    *,
    dataset: str,
    symbol: str,
    start: date,
    end: date,
) -> list[Observation]:
    """Validate daily rows, skip incomplete quotes, keep other sessions.

    The caller may pass ``end`` as today's date. Repeated dates are rejected:
    silent deduplication would obscure provider inconsistencies.
    """
    if frame.empty:
        return []
    if "date" not in frame.columns:
        raise ValueError(f"{dataset} {symbol}: provider omitted date column")
    output: list[Observation] = []
    seen: set[date] = set()
    for record in frame.to_dict("records"):
        day = pd.Timestamp(record["date"]).date()
        if not start <= day <= end:
            continue
        if day in seen:
            raise ValueError(f"{dataset} {symbol}: duplicate provider date {day}")
        seen.add(day)
        close = _value(record, "close")
        if close is None or close <= 0:
            LOG.warning("SKIP INVALID CLOSE %s %s %s", dataset, symbol, day)
            continue
        volume = _value(record, "volume")
        if volume is not None and volume < 0:
            LOG.warning("SKIP NEGATIVE VOLUME %s %s %s", dataset, symbol, day)
            continue
        output.append(
            Observation(
                dataset=dataset,
                symbol=symbol,
                day=day,
                open=_value(record, "open"),
                high=_value(record, "high"),
                low=_value(record, "low"),
                close=close,
                volume=volume,
                dividend=_value(record, "dividend"),
                split_ratio=_value(record, "split_ratio"),
            )
        )
    return output


class DailyCollector:
    def __init__(self, registry: ProviderRegistry | None = None):
        self.registry = registry or ProviderRegistry()

    def collect(
        self, *, dataset: str, symbol: str, days: int, today: date | None = None
    ) -> list[Observation]:
        if days < 1:
            raise ValueError("days must be positive")
        end_day = today or date.today()
        start_day = end_day - timedelta(days=days)
        provider: PriceHistoryProvider = self.registry.get(dataset)
        frame = provider.history(symbol, start_day, end_day + timedelta(days=1))
        return convert_daily_rows(
            frame, dataset=dataset.upper(), symbol=symbol, start=start_day, end=end_day
        )
