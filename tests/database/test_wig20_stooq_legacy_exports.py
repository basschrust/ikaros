"""Regression: SQL isolation must preserve the old WIG20 backfill imports."""

from __future__ import annotations

from datetime import date
import importlib.util

import pandas as pd
import pytest

from database import index_queries
from ingestion import wig20_stooq


def test_legacy_public_functions_are_importable() -> None:
    from ingestion.wig20_stooq import check_overlaps, insert_missing, parse_csv

    assert callable(check_overlaps)
    assert callable(insert_missing)
    assert callable(parse_csv)
    assert set(("check_overlaps", "insert_missing", "parse_csv")) <= set(wig20_stooq.__all__)


def test_overlap_wrapper_delegates_without_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    passed = []

    def fake_query(connection: object, frame: pd.DataFrame, *, tolerance: float):
        passed.append((connection, frame, tolerance))
        return 1, [(date(2026, 11, 17), 1, 2, "stooq")]

    monkeypatch.setattr(index_queries, "check_index_price_overlaps", fake_query)
    connection = object()
    frame = pd.DataFrame({"date": [date(2026, 11, 17)]})
    result = wig20_stooq.check_overlaps(connection, frame, tolerance=0.25)
    assert result == (1, [(date(2026, 11, 17), 1, 2, "stooq")])
    assert passed == [(connection, frame, 0.25)]


def test_insert_wrapper_delegates_without_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    passed = []

    def fake_query(connection: object, frame: pd.DataFrame, *, batch_size: int) -> int:
        passed.append((connection, frame, batch_size))
        return 9

    monkeypatch.setattr(index_queries, "insert_missing_index_prices", fake_query)
    connection = object()
    frame = pd.DataFrame({"date": [date(2026, 11, 17)]})
    assert wig20_stooq.insert_missing(connection, frame, batch_size=7) == 9
    assert passed == [(connection, frame, 7)]


def test_csv_parser_still_supports_old_stooq_column_aliases() -> None:
    frame = wig20_stooq.parse_csv(
        "Data,Otwarcie,Max,Min,Zamknięcie\n2026-11-17,100,105,98,102\n",
        start=date(2026, 11, 17),
        end=date(2026, 11, 17),
    )
    assert len(frame) == 1
    assert frame.iloc[0]["low"] == 98


def test_original_backfill_script_imports_legacy_functions() -> None:
    if importlib.util.find_spec("run.backfill_wig20_prices") is None:
        pytest.skip("Full Ikaros checkout is not available in isolated test staging")
    from run import backfill_wig20_prices

    assert backfill_wig20_prices.check_overlaps is wig20_stooq.check_overlaps
    assert backfill_wig20_prices.insert_missing is wig20_stooq.insert_missing
