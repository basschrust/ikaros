"""ECB transient failures are retried and long historical ranges are divided safely."""

from datetime import date
from unittest.mock import patch

import pytest
import requests

from ikaros_app.rates_international import ECBESTRProvider


class FakeResponse:
    def __init__(self, body="", status=200):
        self.text = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class FakeSession:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.windows = []

    def get(self, url, *, params, **kwargs):
        assert url.endswith("/EST/B.EU000A2X2A25.WT")
        self.windows.append((params["startPeriod"], params["endPeriod"]))
        return next(self.replies)


def test_transient_504_then_success():
    session = FakeSession(
        [
            FakeResponse(status=504),
            FakeResponse("TIME_PERIOD,OBS_VALUE\n2026-11-09,2.442\n"),
        ]
    )
    with patch("ikaros_app.rates_international.time.sleep"):
        rows = ECBESTRProvider().history(session, date(2026, 11, 9), date(2026, 11, 9))
    assert len(rows) == 1
    assert rows[0].rate == pytest.approx(0.02442)
    assert len(session.windows) == 2


def test_long_request_falls_back_to_short_windows():
    # 120 days: after 3 initial failures, each 60-day window succeeds.
    session = FakeSession(
        [
            FakeResponse(status=504),
            FakeResponse(status=504),
            FakeResponse(status=504),
            FakeResponse("TIME_PERIOD,OBS_VALUE\n2026-01-01,2.1\n"),
            FakeResponse("TIME_PERIOD,OBS_VALUE\n2026-03-02,2.2\n"),
        ]
    )
    with patch("ikaros_app.rates_international.time.sleep"):
        rows = ECBESTRProvider().history(session, date(2026, 1, 1), date(2026, 4, 30))
    assert len(rows) == 2
    assert session.windows[-2:] == [("2026-01-01", "2026-03-01"), ("2026-03-02", "2026-04-30")]


def test_unavailable_window_fails_instead_of_skipping():
    session = FakeSession([FakeResponse(status=504)] * 6)
    with patch("ikaros_app.rates_international.time.sleep"):
        with pytest.raises(RuntimeError, match="2026-01-01"):
            ECBESTRProvider().history(session, date(2026, 1, 1), date(2026, 4, 30))


def test_non_transient_400_is_not_retried():
    session = FakeSession([FakeResponse(status=400)])
    with pytest.raises(requests.HTTPError):
        ECBESTRProvider().history(session, date(2026, 11, 9), date(2026, 11, 9))
    assert len(session.windows) == 1
