from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from run.ingest_index_weights import load_snapshot, persist, source_label

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "wig20_weights_2026-10-22.csv"


def test_snapshot():
    day, idx, rows = load_snapshot(FIXTURE)
    assert (day, idx, len(rows)) == (date(2026, 10, 22), "WIG20", 20)
    assert sum(r["weight_pct"] for r in rows) == Decimal("100")


def test_duplicates_rejected(tmp_path):
    file = tmp_path / "bad.csv"
    file.write_text(FIXTURE.read_text().replace("WIG20,PKO,", "WIG20,PKN,"))
    with pytest.raises(ValueError, match="Duplicate"):
        load_snapshot(file)


def test_weights_total_rejected(tmp_path):
    file = tmp_path / "bad.csv"
    file.write_text(FIXTURE.read_text().replace("PKN,14.23", "PKN,4.23"))
    with pytest.raises(ValueError, match="100%"):
        load_snapshot(file)


def test_source_attribution():
    _, _, rows = load_snapshot(FIXTURE)
    assert "https://" in source_label(rows)


def test_persist_replaces_date_and_closes_prior():
    _, _, rows = load_snapshot(FIXTURE)
    for row in rows:
        row["security_symbol"] = row["ticker"] + ".WA"
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (date(2027, 1, 1),)
    persist(conn, date(2026, 10, 22), "WIG20", rows)
    statements = [call.args[0] for call in cur.execute.call_args_list]
    assert statements[0] == "BEGIN"
    assert any('UPDATE "INDEX_CONSTITUENTS"' in sql for sql in statements)
    assert any('DELETE FROM "INDEX_CONSTITUENTS"' in sql for sql in statements)
    assert statements[-1] == "COMMIT"
    params = cur.executemany.call_args.args[1]
    assert len(params) == 20
    assert params[0][4] == date(2026, 12, 31)
    assert sum(Decimal(str(p[2])) for p in params) == Decimal("1.0")


def test_persist_rolls_back_on_failure():
    _, _, rows = load_snapshot(FIXTURE)
    for row in rows:
        row["security_symbol"] = row["ticker"] + ".WA"
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (None,)
    cur.executemany.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        persist(conn, date(2026, 10, 22), "WIG20", rows)
    assert cur.execute.call_args.args[0] == "ROLLBACK"
