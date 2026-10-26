from datetime import date
import pytest

from ingestion.kafka_inbound import Observation, TOPICS
from run.kafka_sink import persist_and_commit


def sample(**kwargs):
    values = dict(dataset="FX", symbol="EURPLN=X", day=date(2026, 10, 23),
                  open=4.20, high=4.30, low=4.10, close=4.25, volume=None)
    values.update(kwargs)
    return Observation(**values)


def test_topics():
    assert TOPICS == {"FX": "ikaros.fx.raw.v1", "GPW": "ikaros.gpw.raw.v1"}


def test_round_trip():
    o = sample()
    assert Observation.deserialize(o.serialize()) == o


def test_ids():
    assert sample().key == "FX:EURPLN=X"
    assert sample().observation_id == "FX:EURPLN=X:2026-10-23"


@pytest.mark.parametrize("overrides", [
    {"dataset": "FOO"}, {"symbol": ""}, {"close": 0.0},
    {"close": float("nan")}, {"volume": -4.0},
])
def test_invalid(overrides):
    with pytest.raises(ValueError):
        sample(**overrides)


def test_reject_corrupt_id():
    data = sample().serialize().replace(b"2026-10-23", b"2026-10-22", 1)
    with pytest.raises(ValueError):
        Observation.deserialize(data)


class Message:
    def __init__(self, event):
        self.event = event

    def value(self):
        return self.event.serialize()

    def key(self):
        return self.event.key.encode()


class Consumer:
    def __init__(self):
        self.commits = 0

    def commit(self, **kwargs):
        self.commits += 1


def test_commit_only_after_success(monkeypatch):
    c = Consumer()
    event = sample()
    monkeypatch.setattr("run.kafka_sink.upsert_observation", lambda conn, event: None)
    assert persist_and_commit(c, object(), Message(event), dataset="FX") == event
    assert c.commits == 1

    def fail(conn, event):
        raise RuntimeError("snowflake down")

    monkeypatch.setattr("run.kafka_sink.upsert_observation", fail)
    with pytest.raises(RuntimeError):
        persist_and_commit(c, object(), Message(event), dataset="FX")
    assert c.commits == 1
