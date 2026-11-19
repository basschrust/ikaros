"""Staging orchestration may persist candidate evidence but cannot promote it."""

from datetime import date
from decimal import Decimal

from database.gpw_price_pipeline import Mapping
from database.market_quote_pipeline import ProviderMapping
from ingestion.gpw_provider_feeds import RawCandle
from ingestion.market_quote_feeds import MarketCandle
from ingestion import candidate_stage_job as job

DAY = date(2026, 11, 18)


def test_gpw_feeds_write_only_candidate_rows(monkeypatch):
    mapping = Mapping(42, "PZU.WA", "YFINANCE", 1, 12, "PZU.WA", 10, date(2020, 1, 1), None)
    monkeypatch.setattr(job, "equity_mappings", lambda _conn, **kw: {42: [mapping]})

    class Feed:
        def history(self, *_args):
            return [
                RawCandle(DAY, Decimal("60"), Decimal("63"), Decimal("59"), Decimal("61"), 12500)
            ]

    monkeypatch.setattr(job, "equity_feeds", lambda: {"YFINANCE": Feed()})
    writes = []
    monkeypatch.setattr(
        job,
        "store_equity_candidates",
        lambda _conn, candidates, **kw: writes.extend(candidates) or 1,
    )
    report = job.run_stage(
        object(),
        dataset="GPW",
        symbols=["PZU.WA"],
        start=DAY,
        end=DAY,
        backend="snowflake",
        write=True,
    )
    assert (report.instruments, report.observations, report.newly_stored) == (1, 1, 1)
    assert writes[0].status == "VALID"
    assert writes[0].mapping.asset_id == 42


def test_derivative_feed_persists_only_candidate(monkeypatch):
    mapping = ProviderMapping(
        "FW20Z2620", 2, "BIZNESRADAR", 3, "FW20Z2620", 30, date(2020, 1, 1), None
    )
    monkeypatch.setattr(job, "market_mappings", lambda _conn, _kind, **kw: {"FW20Z2620": [mapping]})

    class Feed:
        def history(self, *_args):
            return [
                MarketCandle(
                    DAY, Decimal("3200"), Decimal("3220"), Decimal("3190"), Decimal("3205"), 300
                )
            ]

    monkeypatch.setattr(job, "market_feeds", lambda _kind: {"BIZNESRADAR": Feed()})
    recorded = []
    monkeypatch.setattr(
        job, "store_market_candidate", lambda _conn, **kw: recorded.append(kw["candidate"]) or True
    )
    report = job.run_stage(
        object(),
        dataset="DERIVATIVES",
        symbols=["FW20Z2620"],
        start=DAY,
        end=DAY,
        backend="snowflake",
        write=True,
    )
    assert report.newly_stored == 1
    assert recorded[0].validation_status == "VALID"
    assert recorded[0].quote.volume == 300


def test_write_false_never_calls_candidate_store(monkeypatch):
    mapping = ProviderMapping("WIG20", 2, "BIZNESRADAR", 4, "WIG20", 30, date(2020, 1, 1), None)
    monkeypatch.setattr(job, "market_mappings", lambda _conn, _kind, **kw: {"WIG20": [mapping]})

    class Feed:
        def history(self, *_args):
            return [
                MarketCandle(
                    DAY, Decimal("3200"), Decimal("3220"), Decimal("3190"), Decimal("3205")
                )
            ]

    monkeypatch.setattr(job, "market_feeds", lambda _kind: {"BIZNESRADAR": Feed()})
    monkeypatch.setattr(
        job,
        "store_market_candidate",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("unexpected write")),
    )
    report = job.run_stage(
        object(),
        dataset="WIG20",
        symbols=["WIG20"],
        start=DAY,
        end=DAY,
        backend="snowflake",
        write=False,
    )
    assert report.observations == 1 and report.newly_stored == 0


def test_equity_candidate_database_failure_is_not_reported_as_provider_error(monkeypatch):
    import pytest

    mapping = Mapping(42, "PZU.WA", "YFINANCE", 1, 12, "PZU.WA", 10, date(2020, 1, 1), None)
    monkeypatch.setattr(job, "equity_mappings", lambda _conn, **kw: {42: [mapping]})

    class Feed:
        def history(self, *_args):
            return [
                RawCandle(DAY, Decimal("60"), Decimal("63"), Decimal("59"), Decimal("61"), 12500)
            ]

    monkeypatch.setattr(job, "equity_feeds", lambda: {"YFINANCE": Feed()})

    def failed_write(*_args, **_kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(job, "store_equity_candidates", failed_write)
    with pytest.raises(RuntimeError, match="database write failed"):
        job.run_stage(
            object(),
            dataset="GPW",
            symbols=["PZU.WA"],
            start=DAY,
            end=DAY,
            backend="snowflake",
            write=True,
        )


def test_market_candidate_database_failure_is_not_reported_as_provider_error(monkeypatch):
    import pytest

    mapping = ProviderMapping("WIG20", 2, "BIZNESRADAR", 4, "WIG20", 30, date(2020, 1, 1), None)
    monkeypatch.setattr(job, "market_mappings", lambda _conn, _kind, **kw: {"WIG20": [mapping]})

    class Feed:
        def history(self, *_args):
            return [
                MarketCandle(
                    DAY, Decimal("3200"), Decimal("3220"), Decimal("3190"), Decimal("3205")
                )
            ]

    monkeypatch.setattr(job, "market_feeds", lambda _kind: {"BIZNESRADAR": Feed()})

    def failed_write(*_args, **_kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(job, "store_market_candidate", failed_write)
    with pytest.raises(RuntimeError, match="database write failed"):
        job.run_stage(
            object(),
            dataset="WIG20",
            symbols=["WIG20"],
            start=DAY,
            end=DAY,
            backend="snowflake",
            write=True,
        )
