"""SQL-backed GPW writer integration and replay with integer PKs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from database.gpw_price_pipeline import load_mappings
from ingestion.gpw_multi_source_job import run_job
from ingestion.gpw_provider_feeds import RawCandle
from _gpw_sqlite_support import Database

DAY = date(2026, 11, 17)


class Feed:
    def __init__(self, code, volume, close="75.00"):
        self.code = code
        self.volume = volume
        self.close = close
        self.called = []

    def history(self, symbol, start, end):
        self.called.append((symbol, start, end))
        return [
            RawCandle(
                DAY,
                Decimal("75.96"),
                Decimal("75.96"),
                Decimal("74.78"),
                Decimal(self.close),
                self.volume,
            )
        ]


def feeds(stooq=1247844, br=1247844, yahoo=0):
    return {
        "YFINANCE": Feed("YFINANCE", yahoo),
        "STOOQ": Feed("STOOQ", stooq),
        "BIZNESRADAR": Feed("BIZNESRADAR", br),
    }


def run(db, source, *, write=True):
    return run_job(
        db, feeds=source, symbols=["PZU.WA"], start=DAY, end=DAY, write=write, request_delay=0
    )


def test_pzu_suspect_volume_repaired_and_integer_id_references_candidate():
    db = Database(existing_volume=0)
    src = feeds()
    result = run(db, src)
    assert result.promoted == 1 and result.candidates_inserted == 3
    assert not result.unresolved
    statuses = db.db.execute(
        "SELECT VALIDATION_STATUS FROM PRICES_CANDIDATES ORDER BY PROVIDER_ID"
    ).fetchall()
    assert statuses == [("REJECTED",), ("VALID",), ("VALID",)]
    row = db.db.execute("SELECT ASSET_ID,PROVIDER_ID,VOLUME,CANDIDATE_ID FROM PRICES").fetchone()
    assert row[:3] == (101, 2, 1247844)
    assert isinstance(row[3], int) and row[3] > 0
    hash_value = db.db.execute(
        "SELECT CONTENT_HASH FROM PRICES_CANDIDATES WHERE CANDIDATE_ID=?", (row[3],)
    ).fetchone()[0]
    assert len(hash_value) == 64
    assert src["STOOQ"].called[0][0] == "pzu"
    assert src["BIZNESRADAR"].called[0][0] == "PZU"
    db.close()


def test_second_run_does_not_add_candidates_or_change_selected_integer_id():
    db = Database()
    assert run(db, feeds()).promoted == 1
    snapshot = db.db.execute("SELECT * FROM PRICES").fetchall()
    again = run(db, feeds())
    assert again.candidates_inserted == 0
    assert db.db.execute("SELECT * FROM PRICES").fetchall() == snapshot
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3
    db.close()


def test_no_write_on_dry_run():
    db = Database()
    result = run(db, feeds(), write=False)
    assert result.days_seen == 1 and result.candidates_inserted == 0
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 0
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 0
    db.close()


def test_valid_existing_conflict_is_preserved_but_candidates_saved():
    db = Database(existing_volume=5000000)
    result = run(db, feeds())
    assert result.preserved_conflicts == 1
    assert db.db.execute("SELECT VOLUME FROM PRICES").fetchone() == (5000000,)
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3
    db.close()


def test_valid_disagreement_selects_priority_and_integer_fk():
    db = Database()
    result = run(db, feeds(stooq=1247844, br=2000000))
    assert result.unresolved == 0 and result.promoted == 1
    row = db.db.execute(
        "SELECT PROVIDER_ID,VOLUME,SELECTION_REASON,CANDIDATE_ID FROM PRICES"
    ).fetchone()
    assert row[:3] == (2, 1247844, "PRIORITY_SELECTED_DISAGREEMENT")
    assert type(row[3]) is int
    assert db.db.execute("SELECT COUNT(*) FROM PRICES_CANDIDATES").fetchone()[0] == 3
    db.close()


def test_one_active_provider_can_promote():
    db = Database()
    db.db.execute('UPDATE PROVIDERS SET IS_ACTIVE=0 WHERE PROVIDER_CODE IN ("YFINANCE","STOOQ")')
    result = run(db, feeds())
    assert result.symbols_processed == 1 and result.promoted == 1
    assert result.provider_requests == 1
    assert db.db.execute("SELECT PROVIDER_ID FROM PRICES").fetchone() == (3,)
    db.close()


def test_missing_symbol_refuses_ingestion():
    db = Database()
    with pytest.raises(ValueError, match="Missing active GPW price mappings"):
        load_mappings(db, symbols=["UNKNOWN.WA"])
    db.close()


def test_no_sources_does_not_report_success():
    db = Database()
    source = feeds()
    for f in source.values():
        f.history = lambda *args: []
    result = run(db, source)
    assert result.days_seen == 0 and result.promoted == 0
    assert len(result.provider_errors) == 1
    assert db.db.execute("SELECT COUNT(*) FROM PRICES").fetchone()[0] == 0
    db.close()
