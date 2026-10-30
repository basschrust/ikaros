
"""Regression tests: invalid provider closes must not discard valid sessions."""

from datetime import date, timedelta

import pandas as pd
import pytest

from ingestion.kafka_inbound import Observation
from ikaros_app.providers import OpenBBEquityProvider
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
            {
                "date": today,
                "open": 13.0,
                "high": 14.0,
                "low": 12.8,
                "close": 13.5,
                "volume": 200,
            },
        ]
    )

    # Mock the new provider boundary, not the legacy collector module.
    def mock_history(self, symbol, start, end):
        assert symbol == "ANR.WA"
        assert start <= today - timedelta(days=2)
        assert end > today
        return frame

    monkeypatch.setattr(OpenBBEquityProvider, "history", mock_history)

    events = kafka_collect.observations(
        dataset="GPW",
        symbol="ANR.WA",
        days=7,
    )

    assert len(events) == 2
    assert [event.close for event in events] == [12.5, 13.5]
    assert "SKIP INVALID CLOSE GPW ANR.WA" in caplog.text


@pytest.mark.parametrize(
    "close",
    [None, 0.0, -1.0, float("nan"), float("inf")],
)
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
