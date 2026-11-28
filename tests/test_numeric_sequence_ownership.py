"""Sequence ownership regression tests for safe numeric DuckDB rebuilds.

The derivative price candidate table has *two* ID fields. Its generated
sequence must use CANDIDATE_ID, never the unrelated PROVIDER_SYMBOL_ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from database.numeric_migration import (
    SEQUENCE_OWNERS,
    UnsafeNumericMigration,
    _sequence_starts,
    rewrite_export_sequences,
)


@dataclass
class Result:
    rows: list[tuple[Any, ...]]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...]:
        return self.rows[0]


class SequenceCatalog:
    def __init__(
        self,
        *,
        name: str = "DERIVATIVE_PRICE_CANDIDATES_ID_SEQ",
        last: int | None = 19,
        max_id: int = 12,
        increment: int = 1,
        cycle: bool = False,
    ) -> None:
        self.name = name
        self.last = last
        self.max_id = max_id
        self.increment = increment
        self.cycle = cycle
        self.max_column: str | None = None

    def execute(self, query: str, params: tuple[str, str] = ()) -> Result:
        if "FROM duckdb_sequences()" in query:
            return Result([("main", self.name, 1, self.increment, self.last, self.cycle)])
        if "FROM information_schema.tables" in query:
            return Result([("main", "DERIVATIVE_PRICE_CANDIDATES")])
        if "FROM information_schema.columns" in query:
            assert params == ("MAIN", "DERIVATIVE_PRICE_CANDIDATES")
            return Result([("CANDIDATE_ID",), ("PROVIDER_SYMBOL_ID",)])
        if "SELECT COALESCE(MAX(" in query:
            if 'MAX("CANDIDATE_ID")' in query:
                self.max_column = "CANDIDATE_ID"
                return Result([(self.max_id,)])
            raise AssertionError(f"Wrong ID field selected: {query}")
        raise AssertionError(f"Unexpected SQL: {query}")


def test_derivative_candidate_sequence_uses_candidate_id_despite_two_id_fields() -> None:
    catalog = SequenceCatalog(last=19, max_id=12)
    assert _sequence_starts(catalog) == {"DERIVATIVE_PRICE_CANDIDATES_ID_SEQ": 20}
    assert catalog.max_column == "CANDIDATE_ID"


def test_sequence_start_accounts_for_all_stored_ids() -> None:
    catalog = SequenceCatalog(last=19, max_id=1234)
    assert _sequence_starts(catalog)["DERIVATIVE_PRICE_CANDIDATES_ID_SEQ"] == 1235


def test_never_reuses_ids_when_rows_are_deleted() -> None:
    catalog = SequenceCatalog(last=5000, max_id=12)
    assert _sequence_starts(catalog)["DERIVATIVE_PRICE_CANDIDATES_ID_SEQ"] == 5001


def test_unused_sequence_must_still_skip_manually_inserted_ids() -> None:
    catalog = SequenceCatalog(last=None, max_id=88)
    assert _sequence_starts(catalog)["DERIVATIVE_PRICE_CANDIDATES_ID_SEQ"] == 89


def test_unknown_used_sequence_is_not_silently_reset() -> None:
    catalog = SequenceCatalog(name="UNREVIEWED_ID_SEQ", last=4)
    with pytest.raises(UnsafeNumericMigration, match="no declared ID owner"):
        _sequence_starts(catalog)


def test_unknown_unused_sequence_is_preserved_as_exported() -> None:
    catalog = SequenceCatalog(name="UNREVIEWED_ID_SEQ", last=None)
    assert _sequence_starts(catalog) == {}


def test_cycling_or_nonunit_sequence_is_rejected() -> None:
    with pytest.raises(UnsafeNumericMigration, match="monotonic"):
        _sequence_starts(SequenceCatalog(cycle=True))
    with pytest.raises(UnsafeNumericMigration, match="monotonic"):
        _sequence_starts(SequenceCatalog(increment=2))


def test_all_existing_ikaros_sequence_families_mapped() -> None:
    assert SEQUENCE_OWNERS["DERIVATIVE_PRICE_CANDIDATES_ID_SEQ"] == (
        "DERIVATIVE_PRICE_CANDIDATES",
        "CANDIDATE_ID",
    )
    assert SEQUENCE_OWNERS["INDEX_PROVIDER_SYMBOLS_ID_SEQ"] == (
        "INDEX_PROVIDER_SYMBOLS",
        "PROVIDER_SYMBOL_ID",
    )
    assert SEQUENCE_OWNERS["FX_RATE_CANDIDATES_ID_SEQ"] == ("FX_RATE_CANDIDATES", "CANDIDATE_ID")


def test_export_sequence_rewrite_produces_collision_free_next_value() -> None:
    sql = 'CREATE SEQUENCE "main"."DERIVATIVE_PRICE_CANDIDATES_ID_SEQ" START 1 INCREMENT BY 1;\n'
    actual = rewrite_export_sequences(sql, {"DERIVATIVE_PRICE_CANDIDATES_ID_SEQ": 1235})
    assert "START WITH 1235 INCREMENT BY 1;" in actual


def test_rebuild_roundtrip_keeps_counters_above_original_records(tmp_path) -> None:
    duckdb = pytest.importorskip("duckdb")
    from database.numeric_migration import rebuild

    source = tmp_path / "source.duckdb"
    output = tmp_path / "candidate.duckdb"
    with duckdb.connect(str(source)) as con:
        con.execute(
            'CREATE TABLE "SECURITIES" ("ASSET_ID" BIGINT PRIMARY KEY, "REFERENCE_PRICE" DOUBLE)'
        )
        con.execute('INSERT INTO "SECURITIES" VALUES (100, NULL)')
        con.execute(
            'CREATE TABLE "PROVIDER_SYMBOLS" ("PROVIDER_SYMBOL_ID" BIGINT PRIMARY KEY, '
            '"ASSET_ID" BIGINT REFERENCES "SECURITIES"("ASSET_ID"))'
        )
        con.execute('INSERT INTO "PROVIDER_SYMBOLS" VALUES (7, 100)')
        con.execute('CREATE SEQUENCE "DERIVATIVE_PRICE_CANDIDATES_ID_SEQ" START 1')
        con.execute(
            'CREATE TABLE "DERIVATIVE_PRICE_CANDIDATES" ("CANDIDATE_ID" BIGINT PRIMARY KEY, '
            '"PROVIDER_SYMBOL_ID" BIGINT, "CLOSE" DECIMAL(24,8))'
        )
        con.execute(
            'INSERT INTO "DERIVATIVE_PRICE_CANDIDATES" VALUES '
            "(nextval('DERIVATIVE_PRICE_CANDIDATES_ID_SEQ'), 7, 123.45)"
        )
        con.execute('INSERT INTO "DERIVATIVE_PRICE_CANDIDATES" VALUES (101, 7, 123.55)')
        con.execute('CREATE TABLE "PRICES" ("DIVIDEND" DOUBLE)')
        con.execute('INSERT INTO "PRICES" VALUES (NULL)')
    assert rebuild(source, output=output) == 0
    assert source.is_file() and output.is_file()
    with duckdb.connect(str(output)) as con:
        new_id = con.execute("SELECT nextval('DERIVATIVE_PRICE_CANDIDATES_ID_SEQ')").fetchone()[0]
        assert new_id >= 102
        assert con.execute('SELECT COUNT(*) FROM "DERIVATIVE_PRICE_CANDIDATES"').fetchone()[0] == 2
        assert (
            con.execute('SELECT typeof("REFERENCE_PRICE") FROM "SECURITIES"').fetchone()[0]
            == "DECIMAL(24,8)"
        )
