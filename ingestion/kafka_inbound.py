"""Stable inbound daily market-data event contract. No Kafka dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import math

TOPICS = {"FX": "ikaros.fx.raw.v1", "GPW": "ikaros.gpw.raw.v1"}


@dataclass(frozen=True)
class Observation:
    dataset: str
    symbol: str
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None = None
    dividend: float | None = None
    split_ratio: float | None = None
    provider: str = "yfinance"

    def __post_init__(self):
        if self.dataset not in TOPICS or not self.symbol or not self.provider:
            raise ValueError("Invalid dataset, symbol or provider")
        if not isinstance(self.day, date) or isinstance(self.day, datetime):
            raise ValueError("day must be date")
        for name in ("open", "high", "low", "close", "volume", "dividend", "split_ratio"):
            number = getattr(self, name)
            if number is not None and not math.isfinite(float(number)):
                raise ValueError(f"Non-finite {name}")
        if self.close <= 0 or (self.volume is not None and self.volume < 0):
            raise ValueError("Invalid close or volume")

    @property
    def key(self):
        return f"{self.dataset}:{self.symbol}"

    @property
    def observation_id(self):
        return f"{self.key}:{self.day.isoformat()}"

    def serialize(self) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "dataset": self.dataset,
                "symbol": self.symbol,
                "date": self.day.isoformat(),
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "dividend": self.dividend,
                "split_ratio": self.split_ratio,
                "provider": self.provider,
                "observation_id": self.observation_id,
            },
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def deserialize(cls, payload: bytes) -> "Observation":
        obj = json.loads(payload)
        if obj.get("version") != 1:
            raise ValueError("Unsupported event version")
        required = {"dataset", "symbol", "date", "close", "observation_id"}
        if not required.issubset(obj):
            raise ValueError(f"Missing fields: {required - set(obj)}")
        event = cls(
            dataset=obj["dataset"],
            symbol=obj["symbol"],
            day=date.fromisoformat(obj["date"]),
            open=obj.get("open"),
            high=obj.get("high"),
            low=obj.get("low"),
            close=obj["close"],
            volume=obj.get("volume"),
            dividend=obj.get("dividend"),
            split_ratio=obj.get("split_ratio"),
            provider=obj.get("provider", "yfinance"),
        )
        if event.observation_id != obj["observation_id"]:
            raise ValueError("observation_id mismatch")
        return event
