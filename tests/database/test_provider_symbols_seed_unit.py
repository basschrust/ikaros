"""Unit checks for provider-symbol reference planning and safety rules."""

from __future__ import annotations

import csv
from datetime import date

import pytest

from database.provider_symbols_seed import (
    EARLIEST_MAPPING_DATE,
    ExistingMapping,
    Security,
    SymbolMapping,
    plan_seed,
    read_reviewed_csv,
    wanted_mappings,
)


def _securities() -> dict[str, Security]:
    return {
        "PZU.WA": Security(101, "PZU.WA", "GPW", "stock", True),
        "PKO.WA": Security(102, "PKO.WA", "GPW", "stock", True),
        "BAD.WA": Security(103, "BAD.WA", "GPW", "stock", False),
        "FW20": Security(104, "FW20", "GPW", "future", True),
    }


def _csv(tmp_path, rows):
    path = tmp_path / "map.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "canonical_symbol",
                "venue",
                "provider_code",
                "provider_symbol",
                "valid_from",
                "valid_to",
            )
        )
        writer.writerows(rows)
    return path


def test_automatic_mappings_only_for_active_gpw_equities():
    mapping = wanted_mappings(_securities(), venue="GPW", reviewed=[])
    assert mapping == [
        SymbolMapping(101, "YFINANCE", "PZU.WA", EARLIEST_MAPPING_DATE),
        SymbolMapping(102, "YFINANCE", "PKO.WA", EARLIEST_MAPPING_DATE),
    ]


def test_curated_csv_maps_different_external_symbols_to_one_asset(tmp_path):
    path = _csv(
        tmp_path,
        [
            ("PZU.WA", "GPW", "STOOQ", "pzu", "1900-01-01", ""),
            ("PZU.WA", "GPW", "BIZNESRADAR", "PZU", "1900-01-01", ""),
        ],
    )
    parsed = read_reviewed_csv(path, _securities(), "GPW")
    assert [(x.asset_id, x.provider_code, x.provider_symbol) for x in parsed] == [
        (101, "STOOQ", "pzu"),
        (101, "BIZNESRADAR", "PZU"),
    ]
    combined = wanted_mappings(_securities(), venue="GPW", reviewed=parsed)
    plan = plan_seed({}, [], combined)
    assert len(plan.providers) == 3
    assert len(plan.mappings) == 4


@pytest.mark.parametrize(
    "row,reason",
    [
        (("UNKNOWN.WA", "GPW", "STOOQ", "foo", "1900-01-01", ""), "unknown/inactive"),
        (("BAD.WA", "GPW", "STOOQ", "bad", "1900-01-01", ""), "unknown/inactive"),
        (("PZU.WA", "GPW", "UNREGISTERED", "pzu", "1900-01-01", ""), "unknown provider"),
        (("PZU.WA", "US", "STOOQ", "pzu", "1900-01-01", ""), "unexpected venue"),
        (("PZU.WA", "GPW", "STOOQ", "pzu", "2026-11-18", "2020-01-01"), "valid_to"),
        (("PZU.WA", "GPW", "STOOQ", "", "1900-01-01", ""), "invalid provider symbol"),
        (("PZU.WA", "GPW", "STOOQ", "pzu", "NONSENSE", ""), "Invalid ISO date"),
    ],
)
def test_csv_fails_closed(tmp_path, row, reason):
    with pytest.raises(ValueError, match=reason):
        read_reviewed_csv(_csv(tmp_path, [row]), _securities(), "GPW")


def test_alias_cannot_point_to_different_asset_on_overlapping_dates():
    previous = [ExistingMapping(77, 102, 2, "pzu", date(1900, 1, 1), None)]
    with pytest.raises(ValueError, match="already maps to asset"):
        plan_seed(
            {"YFINANCE": 1, "STOOQ": 2, "BIZNESRADAR": 3},
            previous,
            [SymbolMapping(101, "STOOQ", "pzu", date(1900, 1, 1))],
        )


def test_same_asset_cannot_have_two_overlapping_aliases_at_one_provider():
    previous = [ExistingMapping(77, 101, 2, "pzu", date(1900, 1, 1), None)]
    with pytest.raises(ValueError, match="Conflicting provider symbols"):
        plan_seed(
            {"YFINANCE": 1, "STOOQ": 2, "BIZNESRADAR": 3},
            previous,
            [SymbolMapping(101, "STOOQ", "not-pzu", date(2026, 1, 1))],
        )


def test_disjoint_date_ranges_can_reuse_provider_symbol():
    old = [ExistingMapping(77, 102, 2, "ABC", date(2000, 1, 1), date(2019, 12, 31))]
    plan = plan_seed(
        {"YFINANCE": 1, "STOOQ": 2, "BIZNESRADAR": 3},
        old,
        [SymbolMapping(101, "STOOQ", "ABC", date(2020, 1, 1))],
    )
    assert len(plan.mappings) == 1


def test_identical_existing_mapping_not_reinserted():
    current = [ExistingMapping(100, 101, 1, "PZU.WA", EARLIEST_MAPPING_DATE, None)]
    plan = plan_seed(
        {"YFINANCE": 1, "STOOQ": 2, "BIZNESRADAR": 3},
        current,
        [SymbolMapping(101, "YFINANCE", "PZU.WA", EARLIEST_MAPPING_DATE)],
    )
    assert plan.existing_count == 1
    assert not plan.mappings
    assert not plan.providers


def test_duplicate_reviewed_mappings_do_not_create_duplicate_rows():
    mapping = SymbolMapping(101, "STOOQ", "pzu", date(1900, 1, 1))
    requested = wanted_mappings(_securities(), venue="GPW", reviewed=[mapping, mapping])
    assert requested.count(mapping) == 1


def test_auto_mapping_rejects_non_yahoo_symbol_format():
    with pytest.raises(ValueError, match="lacks reviewed Yahoo"):
        wanted_mappings({"PZU": Security(1, "PZU", "GPW", "stock", True)}, venue="GPW", reviewed=[])


def test_existing_broader_validity_window_satisfies_mapping():
    from datetime import date
    from database.provider_symbols_seed import plan_seed, ExistingMapping, SymbolMapping

    existing = [ExistingMapping(42, 101, 7, "PZU", date(1900, 1, 1), None)]
    plan = plan_seed(
        {"BIZNESRADAR": 7}, existing, [SymbolMapping(101, "BIZNESRADAR", "PZU", date(2010, 5, 11))]
    )
    assert not plan.mappings
    assert plan.existing_count == 1
