"""P0 regression: only the independent promoter can write authoritative prices."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from database.final_write_guard import (
    CandidateBoundaryError,
    GuardedConnection,
    _write_targets,
    protect_repository,
)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ('INSERT INTO "PRICES" ("SYMBOL") VALUES (?)', {"PRICES"}),
        (
            "MERGE INTO FX_RATES t USING source s ON TRUE WHEN MATCHED THEN UPDATE SET x=1",
            {"FX_RATES"},
        ),
        ('UPDATE "DAEDALUS"."MARKET_DATA"."INDEX_PRICES" SET "CLOSE"=1', {"INDEX_PRICES"}),
        ("DELETE FROM DERIVATIVE_PRICES WHERE 1=1", {"DERIVATIVE_PRICES"}),
        ('TRUNCATE TABLE "RATE_OBSERVATIONS"', {"RATE_OBSERVATIONS"}),
        ('DROP TABLE IF EXISTS "PRICES"', {"PRICES"}),
        ('INSERT OVERWRITE INTO "PRICES" VALUES (1)', {"PRICES"}),
        ('COPY INTO "FX_RATES" FROM @staged', {"FX_RATES"}),
        ('INSERT INTO "PRICES_CANDIDATES" VALUES (1)', set()),
        ('SELECT * FROM "PRICES"', set()),
        ("-- MERGE INTO PRICES\nSELECT 1", set()),
    ],
)
def test_target_detection(sql: str, expected: set[str]):
    assert _write_targets(sql) == expected


def test_legacy_write_blocked_even_if_caller_uses_cursor():
    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "PRICES" ("SYMBOL" TEXT)')
    conn = GuardedConnection(db)
    with pytest.raises(CandidateBoundaryError, match="Direct authoritative"):
        conn.execute('INSERT INTO "PRICES" VALUES ("PZU.WA")')
    with pytest.raises(CandidateBoundaryError):
        with conn.cursor() as cur:
            cur.execute('INSERT INTO "PRICES" VALUES ("PZU.WA")')
    assert db.execute('SELECT COUNT(*) FROM "PRICES"').fetchone() == (0,)
    db.close()


def test_candidate_table_is_writable_and_replay_select_is_allowed():
    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "PRICES_CANDIDATES" ("CANDIDATE_ID" INTEGER)')
    conn = GuardedConnection(db)
    conn.execute('INSERT INTO "PRICES_CANDIDATES" VALUES (1)')
    assert conn.execute('SELECT "CANDIDATE_ID" FROM "PRICES_CANDIDATES"').fetchall() == [(1,)]
    db.close()


def test_bulk_pandas_bypass_is_blocked():
    db = sqlite3.connect(":memory:")
    calls = []
    repo = SimpleNamespace(connection=db, _insert_frame=lambda t, f: calls.append(t))
    protect_repository(repo)
    with pytest.raises(CandidateBoundaryError, match="bulk"):
        repo._insert_frame("prices", object())
    repo._insert_frame("securities", object())
    assert calls == ["securities"]
    protect_repository(repo)
    repo._insert_frame("currencies", object())
    assert calls == ["securities", "currencies"]
    db.close()


def test_guard_does_not_treat_sql_comments_as_writes():
    assert _write_targets("SELECT 1 /* INSERT INTO FX_RATES */ -- UPDATE PRICES") == set()


def test_only_promoter_call_stack_may_write_final_rows():
    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "PRICES" ("SYMBOL" TEXT)')
    conn = GuardedConnection(db)
    ns = {"__name__": "database.candidate_promotion", "conn": conn}
    exec('def promote_dataset():\n    conn.execute(\'INSERT INTO "PRICES" VALUES ("PZU.WA")\')', ns)
    ns["promote_dataset"]()
    assert conn.execute('SELECT COUNT(*) FROM "PRICES"').fetchone() == (1,)
    ns2 = {"__name__": "database.candidate_observations", "conn": conn}
    exec('def stage():\n    conn.execute(\'INSERT INTO "PRICES" VALUES ("KGH.WA")\')', ns2)
    with pytest.raises(CandidateBoundaryError):
        ns2["stage"]()
    assert conn.execute('SELECT COUNT(*) FROM "PRICES"').fetchone() == (1,)
    db.close()


def test_duckdb_convenience_sql_and_query_paths_are_blocked():
    class Connection:
        def sql(self, sql):
            raise AssertionError(f"not expected to execute {sql}")

        def query(self, sql):
            raise AssertionError(f"not expected to execute {sql}")

    guarded = GuardedConnection(Connection())
    with pytest.raises(CandidateBoundaryError):
        guarded.sql("INSERT INTO PRICES VALUES (1)")
    with pytest.raises(CandidateBoundaryError):
        guarded.query("DELETE FROM FX_RATES")
