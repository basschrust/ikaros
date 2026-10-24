from datetime import date, datetime, timedelta, timezone

import pytest

from ingestion.iic import BatchKey, BatchState, Status, should_run


@pytest.fixture
def key():
    return BatchKey("GPW", "06N.WA", "yfinance", date(2024, 1, 1), date(2024, 12, 31))


def test_key_validation(key):
    assert key.symbol == "06N.WA"
    with pytest.raises(ValueError):
        BatchKey("OTHER", "06N.WA", "yfinance", date(2024, 1, 1), date(2024, 12, 31))
    with pytest.raises(ValueError):
        BatchKey("GPW", "06N.WA", "yfinance", date(2024, 12, 31), date(2024, 1, 1))


def test_new_and_success():
    now = datetime.now(timezone.utc)
    assert should_run(None, now=now) == (True, "NEW")
    assert should_run(BatchState(Status.SUCCESS), now=now)[0] is False
    assert should_run(BatchState(Status.SUCCESS), now=now, force=True)[0] is True


def test_no_data_requires_opt_in():
    now = datetime.now(timezone.utc)
    assert should_run(BatchState(Status.NO_DATA), now=now)[0] is False
    assert should_run(BatchState(Status.NO_DATA), now=now, retry_no_data=True)[0] is True


def test_retry_failed_and_expired_running():
    now = datetime.now(timezone.utc)
    assert should_run(BatchState(Status.FAILED), now=now)[0] is True
    recent = BatchState(Status.RUNNING, now - timedelta(minutes=5))
    old = BatchState(Status.RUNNING, now - timedelta(minutes=120))
    assert should_run(recent, now=now)[0] is False
    assert should_run(old, now=now)[0] is True


def test_naive_utc_snowflake_timestamp():
    now = datetime.now(timezone.utc)
    started = (now - timedelta(minutes=3)).replace(tzinfo=None)
    assert should_run(BatchState(Status.RUNNING, started), now=now)[0] is False


def test_invalid_lease():
    with pytest.raises(ValueError, match="positive"):
        should_run(None, now=datetime.now(timezone.utc), lease_minutes=0)
