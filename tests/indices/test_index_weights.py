import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from run.ingest_index_weights import load_snapshot, persist, source_label

# Reuse the versioned official GPW snapshot, not a local untracked file.
FIXTURE = Path(__file__).resolve().parents[2] / "data" / "wig20_gpw_official_sept2026.csv"
AS_OF = date(2026, 9, 21)


def test_snapshot():
    day, idx, rows = load_snapshot(FIXTURE)
    assert (day, idx, len(rows)) == (AS_OF, "WIG20", 20)
    assert sum(r["weight_pct"] for r in rows) == Decimal("100")


def _mutated_snapshot(*, target: Path, mutation: str) -> None:
    with FIXTURE.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = reader.fieldnames
    if mutation == "duplicate":
        rows[0]["ticker"] = rows[1]["ticker"]
    else:
        rows[0]["weight_pct"] = str(Decimal(rows[0]["weight_pct"]) / 2)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_duplicates_rejected(tmp_path):
    file = tmp_path / "bad.csv"
    _mutated_snapshot(target=file, mutation="duplicate")
    with pytest.raises(ValueError, match="Duplicate"):
        load_snapshot(file)


def test_weights_total_rejected(tmp_path):
    file = tmp_path / "bad.csv"
    _mutated_snapshot(target=file, mutation="weight")
    with pytest.raises(ValueError, match="100%"):
        load_snapshot(file)


def test_source_attribution():
    _, _, rows = load_snapshot(FIXTURE)
    assert "https://" in source_label(rows)


def _resolved_rows():
    _, _, rows = load_snapshot(FIXTURE)
    with FIXTURE.open(encoding="utf-8-sig", newline="") as handle:
        canonical = {r["ticker"]: r["security_symbol"] for r in csv.DictReader(handle)}
    for row in rows:
        row["security_symbol"] = canonical[row["ticker"]]
    return rows


def test_persist_replaces_date_and_closes_prior():
    rows = _resolved_rows()
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (date(2027, 1, 1),)
    persist(conn, AS_OF, "WIG20", rows)
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
    rows = _resolved_rows()
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = (None,)
    cur.executemany.side_effect = RuntimeError("write failed")
    with pytest.raises(RuntimeError, match="write failed"):
        persist(conn, AS_OF, "WIG20", rows)
    assert cur.execute.call_args.args[0] == "ROLLBACK"
