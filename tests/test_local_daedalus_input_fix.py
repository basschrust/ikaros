"""Pure unit and lightweight in-memory SQL checks; no remote DB or provider calls."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database.daedalus_reference import ensure_reference_schema
from run.import_local_daedalus_inputs import (
    CONTRACT_FIELDS,
    WEIGHT_FIELDS,
    read_csv,
    store_contracts,
    store_weights,
    validate_contracts,
    validate_weights,
)
from run.import_local_verified_pzu import parse_history

ROOT = Path(__file__).resolve().parents[1]


class InMemorySql:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            'CREATE TABLE "DERIVATIVE_CONTRACTS" ("CONTRACT_ID" VARCHAR PRIMARY KEY, '
            '"INSTRUMENT_TYPE" VARCHAR, "UNDERLYING_SYMBOL" VARCHAR, "EXPIRY_DATE" DATE)'
        )
        self.db.execute('CREATE TABLE "SECURITIES" ("SYMBOL" VARCHAR, "VENUE" VARCHAR)')

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        if "information_schema.columns" in sql:
            return self.db.execute('SELECT name FROM pragma_table_info("DERIVATIVE_CONTRACTS")')
        return self.db.execute(sql, params)


def _snap() -> tuple[date, list[dict]]:
    p = ROOT / "data" / "wig20_gpw_official_sept2026.csv"
    return validate_weights(rows=read_csv(path=p, fields=WEIGHT_FIELDS))


def _contracts() -> list[dict]:
    p = ROOT / "data" / "wig20_gpw_verified_contracts_2026_10.csv"
    return validate_contracts(rows=read_csv(path=p, fields=CONTRACT_FIELDS))


def test_reference_csvs() -> None:
    as_of, items = _snap()
    assert as_of == date(2026, 9, 21)
    assert len(items) == 20
    assert sum((x["weight"] for x in items), Decimal()) == Decimal(1)
    assert {x["symbol"] for x in items} >= {"MDV.WA", "EBP.WA", "PZU.WA"}
    contracts = _contracts()
    assert len([x for x in contracts if x["instrument_type"] == "FUTURE"]) == 4
    assert len([x for x in contracts if x["instrument_type"] == "OPTION"]) == 10
    assert {x["option_side"] for x in contracts if x["instrument_type"] == "OPTION"} == {
        "CALL",
        "PUT",
    }


def test_reject_bad_weights() -> None:
    _, items = _snap()
    assert len(items) == 20
    rows = read_csv(path=ROOT / "data" / "wig20_gpw_official_sept2026.csv", fields=WEIGHT_FIELDS)
    rows[0]["weight_pct"] = "-1"
    with pytest.raises(ValueError, match="strictly positive"):
        validate_weights(rows=rows)


def test_reject_missing_option_metadata() -> None:
    rows = read_csv(
        path=ROOT / "data" / "wig20_gpw_verified_contracts_2026_10.csv", fields=CONTRACT_FIELDS
    )
    rows[-1]["option_side"] = ""
    with pytest.raises(ValueError, match="requires verified strike"):
        validate_contracts(rows=rows)


def test_additive_ddl_and_reference_insert() -> None:
    sqlite3.register_adapter(Decimal, str)
    conn = InMemorySql()
    conn.execute(
        'INSERT INTO "DERIVATIVE_CONTRACTS" VALUES (?,?,?,?)',
        ("FW20Z2620", "FUTURE", "WIG20", "2026-12-18"),
    )
    ensure_reference_schema(conn)
    cols = {r[1] for r in conn.db.execute('PRAGMA table_info("DERIVATIVE_CONTRACTS")')}
    assert {"SYMBOL", "MULTIPLIER", "STRIKE", "OPTION_SIDE"} <= cols
    contracts = _contracts()
    changed = store_contracts(connection=conn, items=contracts)
    assert changed == 14
    assert store_contracts(connection=conn, items=contracts) == 0
    stored = conn.execute(
        'SELECT "SYMBOL","MULTIPLIER" FROM "DERIVATIVE_CONTRACTS" WHERE "CONTRACT_ID"=?',
        ("FW20Z2620",),
    ).fetchone()
    assert stored == ("FW20Z2620", 20)
    assert conn.execute('SELECT COUNT(*) FROM "DERIVATIVE_CONTRACTS"').fetchone()[0] == 14

    as_of, constituents = _snap()
    conn.db.executemany(
        'INSERT INTO "SECURITIES" VALUES (?,?)', [(x["symbol"], "GPW") for x in constituents]
    )
    inserted = store_weights(connection=conn, as_of=as_of, items=constituents)
    assert inserted == 20
    assert conn.execute('SELECT COUNT(*) FROM "INDEX_CONSTITUENTS"').fetchone()[0] == 20


def test_verified_pzu_history() -> None:
    rows = parse_history(path=ROOT / "data/pzu_verified_gap_2026_10_14_to_11_20.csv")
    assert len(rows) == 27
    assert rows[0][0] == date(2026, 10, 14)
    assert rows[-1] == (
        date(2026, 11, 20),
        Decimal("71.00"),
        Decimal("71.04"),
        Decimal("67.00"),
        Decimal("67.46"),
        3574250,
    )
