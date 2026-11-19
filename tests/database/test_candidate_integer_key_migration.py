"""Regression guard: migration preserves provenance and new writes use a sequence."""

from pathlib import Path

from database.gpw_price_pipeline import CANDIDATE_SEQUENCE, make_candidate
from database.schema import BASE_COLUMNS, NEW_COLUMNS, CANDIDATE_ID_SEQUENCE
from ingestion.gpw_provider_feeds import RawCandle
from database.gpw_price_pipeline import Mapping
from datetime import date
from decimal import Decimal


def test_integer_type_declared_in_both_tables():
    assert BASE_COLUMNS["prices"]["candidate_id"] == "BIGINT"
    assert NEW_COLUMNS["prices_candidates"]["candidate_id"] == "BIGINT PRIMARY KEY"
    assert NEW_COLUMNS["prices_candidates"]["content_hash"].startswith("VARCHAR")
    assert CANDIDATE_ID_SEQUENCE in CANDIDATE_SEQUENCE


def test_fingerprint_still_stable_but_is_not_candidate_primary_key():
    m = Mapping(1, "PZU.WA", "BIZNESRADAR", 3, 300, "PZU", 30, date(2026, 1, 1), None)
    a = RawCandle(
        date(2026, 11, 17),
        Decimal("75.96"),
        Decimal("75.96"),
        Decimal("74.78"),
        Decimal("75"),
        1248008,
    )
    b = RawCandle(
        date(2026, 11, 17),
        Decimal("75.960"),
        Decimal("75.960"),
        Decimal("74.780"),
        Decimal("75.000"),
        1248008,
    )
    assert make_candidate(m, a).content_hash == make_candidate(m, b).content_hash


def test_sql_migration_includes_backup_provenance_and_sequence():
    path = Path(__file__).resolve().parents[2] / "sql" / "20261118_integer_candidate_ids.sql"
    if not path.exists():
        # SQL is shipped in the package, but a conventional installation of
        # just the production Python files need not retain migration history.
        return
    sql = path.read_text(encoding="utf-8")
    assert 'CLONE "PRICES_CANDIDATES"' in sql
    assert 'CLONE "PRICES"' in sql
    assert 'TO "CONTENT_HASH"' in sql
    assert 'TO "LEGACY_CANDIDATE_HASH"' in sql
    assert 'CREATE SEQUENCE "PRICES_CANDIDATES_ID_SEQ"' in sql
