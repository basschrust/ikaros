"""Regression coverage: incomplete provider candles must not break a symbol batch."""

from datetime import date, timedelta

import pandas as pd
import pytest

from ingestion.kafka_inbound import Observation
from run import kafka_collect


def test_skips_missing_close_but_keeps_valid_dates(monkeypatch, caplog):
    today = date.today()
    frame = pd.DataFrame(
        [
            {
                "date": today - timedelta(days=2),
                "open": 12.0,
                "high": 13.0,
                "low": 11.0,
                "close": 12.5,
                "volume": 100,
            },
            {
                "date": today - timedelta(days=1),
                "open": 12.5,
                "high": 13.0,
                "low": 12.0,
                "close": None,
                "volume": 0,
            },
            {"date": today, "open": 13.0, "high": 14.0, "low": 12.8, "close": 13.5, "volume": 200},
        ]
    )
    monkeypatch.setattr(kafka_collect, "fetch_eod_prices", lambda **kwargs: frame)
    events = kafka_collect.observations(dataset="GPW", symbol="ANR.WA", days=7)
    assert len(events) == 2
    assert [e.close for e in events] == [12.5, 13.5]
    assert "SKIP INVALID CLOSE GPW ANR.WA" in caplog.text


@pytest.mark.parametrize("close", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_rejects_invalid_close(close):
    with pytest.raises(ValueError):
        Observation(
            dataset="GPW",
            symbol="APE.WA",
            day=date.today(),
            open=None,
            high=None,
            low=None,
            close=close,
        )
