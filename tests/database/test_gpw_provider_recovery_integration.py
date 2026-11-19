"""SQL-backed end-to-end GPW provider recovery with integer candidate references."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ingestion.gpw_multi_source_job import run_job
from ingestion.gpw_provider_feeds import BiznesRadarFeed, RawCandle, StooqFeed
from _gpw_sqlite_support import Database

DAY = date(2026, 11, 17)


class HTTPResponse:
    def __init__(self, content: str):
        self.text = content

    def raise_for_status(self):
        return None


class HTTP:
    def __init__(self, body: str):
        self.body = body

    def get(self, *args, **kwargs):
        return HTTPResponse(self.body)


class Yahoo:
    code = "YFINANCE"

    def __init__(self, volume: int):
        self.volume = volume

    def history(self, symbol, start, end):
        return [
            RawCandle(
                DAY,
                Decimal("75.96"),
                Decimal("75.96"),
                Decimal("74.78"),
                Decimal("75.00"),
                self.volume,
            )
        ]


def make_feeds(*, stooq_error=False, yahoo_volume=0):
    csv = (
        "Download CSV needs API key"
        if stooq_error
        else "Date,Open,High,Low,Close,Volume\n2026-11-17,75.96,75.96,74.78,75.00,1247844\n"
    )
    html = """<html><h1>Notowania historyczne PZU</h1><p>GPW - Akcje</p>
        <table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th>
        <th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr>
        <tr><td>17.11.2026</td><td>75,96</td><td>75,96</td><td>74,78</td>
        <td>75,00</td><td>1 247 844</td><td>1000000</td></tr></table></html>"""
    return {
        "YFINANCE": Yahoo(yahoo_volume),
        "STOOQ": StooqFeed(session=HTTP(csv)),
        "BIZNESRADAR": BiznesRadarFeed(session=HTTP(html)),
    }


def _run(db, feeds, *, write=True, minimum_sources=1):
    return run_job(
        db,
        feeds=feeds,
        symbols=["PZU.WA"],
        start=DAY,
        end=DAY,
        write=write,
        request_delay=0,
        minimum_sources=minimum_sources,
    )


def test_parsers_repair_and_replay_with_integer_id():
    db = Database(existing_volume=0)
    result = _run(db, make_feeds())
    assert result.promoted == 1 and result.candidates_inserted == 3
    assert not result.provider_errors
    row = db.db.execute("SELECT VOLUME, PROVIDER_ID, CANDIDATE_ID FROM PRICES").fetchone()
    assert row[:2] == (1247844, 2)
    assert isinstance(row[2], int) and row[2] > 0
    before = db.db.execute("SELECT * FROM PRICES").fetchall()
    replay = _run(db, make_feeds())
    assert replay.candidates_inserted == 0
    assert db.db.execute("SELECT * FROM PRICES").fetchall() == before
    db.close()


def test_unavailable_stooq_does_not_block_two_valid_sources():
    db = Database()
    result = _run(db, make_feeds(stooq_error=True, yahoo_volume=1247844))
    assert result.promoted == 1 and result.candidates_inserted == 2
    assert len(result.provider_errors) == 1 and "STOOQ" in result.provider_errors[0]
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone() == (1,)
    db.close()


def test_one_valid_provider_promotes_biznesradar():
    db = Database()
    result = _run(db, make_feeds(stooq_error=True, yahoo_volume=0))
    assert result.promoted == 1 and result.unresolved == 0
    row = db.db.execute("SELECT PROVIDER_ID, SELECTION_REASON, CANDIDATE_ID FROM PRICES").fetchone()
    assert row[:2] == (3, "SINGLE_VALID_SOURCE") and isinstance(row[2], int)
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    db.close()


def test_strict_can_be_relaxed_without_duplicate_candidate_insert(caplog):
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)
    caplog.set_level("WARNING")
    strict = _run(db, feeds, minimum_sources=2)
    assert strict.promoted == 0 and strict.unresolved == 1
    assert "ZERO_VOLUME_LIQUID_EQUITY" in caplog.text
    fallback = _run(db, feeds)
    assert fallback.promoted == 1 and fallback.candidates_inserted == 0
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    assert db.db.execute("SELECT CANDIDATE_ID FROM PRICES").fetchone()[0] > 0
    db.close()


def test_rejected_yahoo_disagreement_does_not_block_valid_source():
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)

    class DisagreeingYahoo:
        code = "YFINANCE"

        def history(self, _symbol, _start, _end):
            return [RawCandle(DAY, Decimal("80"), Decimal("80"), Decimal("79"), Decimal("79.5"), 0)]

    feeds["YFINANCE"] = DisagreeingYahoo()
    result = _run(db, feeds)
    assert result.promoted == 1 and result.unresolved == 0
    assert db.db.execute("SELECT PROVIDER_ID, VOLUME FROM PRICES").fetchone() == (3, 1247844)
    db.close()


def test_two_zero_volumes_do_not_promote():
    db = Database()
    feeds = make_feeds(stooq_error=True, yahoo_volume=0)

    class RadarZero:
        code = "BIZNESRADAR"

        def history(self, _symbol, _start, _end):
            return [
                RawCandle(
                    DAY, Decimal("75.96"), Decimal("75.96"), Decimal("74.78"), Decimal("75.00"), 0
                )
            ]

    feeds["BIZNESRADAR"] = RadarZero()
    result = _run(db, feeds)
    assert result.promoted == 0 and result.unresolved == 1
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone() == (0,)
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone() == (2,)
    db.close()
