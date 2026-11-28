"""Offline fixed-point contract and migration-safety tests (no DB server)."""

from unittest.mock import Mock

from database.numeric_contract import (
    COLUMN_TYPES,
    Column,
    classify,
    decimal_parts,
    floating_type,
    normalized_type,
)
from database.schema import BASE_COLUMNS, NEW_COLUMNS, _type
from database.numeric_migration import _cast_expression, audit_payload, preflight_column


def test_fresh_core_schema_never_uses_floating_point() -> None:
    for table, columns in (BASE_COLUMNS | NEW_COLUMNS).items():
        for name, declaration in columns.items():
            assert not floating_type(declaration.split()[0]), (table, name)
    assert BASE_COLUMNS["prices"]["close"] == "DECIMAL(24,8)"
    assert BASE_COLUMNS["prices"]["volume"] == "DECIMAL(38,0)"
    assert BASE_COLUMNS["fx_rates"]["close"] == "DECIMAL(24,10)"
    assert BASE_COLUMNS["securities"]["reference_price"] == "DECIMAL(24,8)"
    assert _type(BASE_COLUMNS["fx_rates"]["close"], "snowflake") == "NUMBER(24,10)"
    assert _type(BASE_COLUMNS["prices"]["volume"], "snowflake") == "NUMBER(38,0)"


def test_contract_covers_all_core_price_domains() -> None:
    for table in (
        "PRICES",
        "PRICES_CANDIDATES",
        "FX_RATES",
        "FX_RATE_CANDIDATES",
        "INDEX_PRICES",
        "INDEX_PRICE_CANDIDATES",
        "DERIVATIVE_PRICES",
        "DERIVATIVE_PRICE_CANDIDATES",
        "RATE_OBSERVATIONS",
        "RATE_OBSERVATION_CANDIDATES",
        "DERIVATIVE_CONTRACTS",
        "US_OPTION_CHAIN_SNAPSHOTS",
        "GPW_STOCK_METRICS_DAILY",
    ):
        assert table in COLUMN_TYPES
    assert COLUMN_TYPES["RATE_OBSERVATIONS"]["RATE"] == "DECIMAL(24,10)"
    assert COLUMN_TYPES["DERIVATIVE_CONTRACTS"]["MULTIPLIER"] == "DECIMAL(18,4)"
    assert COLUMN_TYPES["INDEX_CONSTITUENTS"]["WEIGHT"] == "DECIMAL(24,12)"


def test_unknown_float_blocks_migration() -> None:
    columns = classify(
        [
            ("PRICES", "CLOSE", "DOUBLE"),
            ("SOME_NEW_TABLE", "SURPRISE", "FLOAT"),
        ]
    )
    assert columns[0].needs_conversion
    assert columns[1].unknown_float
    payload = audit_payload(columns, [], [columns[1]])
    assert not payload["safe_to_convert"]
    assert len(payload["floating_columns"]) == 2


def test_non_numeric_quote_column_blocks_migration() -> None:
    item = classify([("PRICES", "CLOSE", "VARCHAR")])[0]
    assert item.unknown_storage
    assert not item.needs_conversion
    assert not audit_payload([item], [], [item])["safe_to_convert"]


def test_decimal_normalization_and_fixed_scale() -> None:
    assert normalized_type("NUMBER(24, 10)") == "DECIMAL(24,10)"
    assert normalized_type("NUMERIC(24,10)") == "DECIMAL(24,10)"
    assert decimal_parts("DECIMAL(38,0)") == (38, 0)
    assert not Column("FX_RATES", "CLOSE", "DECIMAL(24,10)", "DECIMAL(24,10)").needs_conversion
    assert Column("FX_RATES", "CLOSE", "DECIMAL(24,8)", "DECIMAL(24,10)").needs_conversion
    assert _cast_expression(Column("PRICES", "CLOSE", "DOUBLE", "DECIMAL(24,8)")) == (
        'CAST(ROUND("CLOSE", 8) AS DECIMAL(24,8))'
    )


def test_fractional_volume_is_a_hard_preflight_failure() -> None:
    class Connection:
        def execute(self, query):
            if "<> TRUNC" in query:
                return Mock(fetchone=lambda: (3,))
            if 'COUNT("VOLUME")' in query:
                return Mock(fetchone=lambda: (10,))
            return Mock(fetchone=lambda: (0,))

    item = Column("PRICES", "VOLUME", "DOUBLE", "DECIMAL(38,0)")
    scan = preflight_column(Connection(), item)
    assert scan["fractional_count"] == 3
    assert not audit_payload([item], [scan], [])["safe_to_convert"]


def test_float_integer_beyond_2_to_53_is_blocked() -> None:
    class Connection:
        def execute(self, query):
            if "9007199254740991" in query:
                return Mock(fetchone=lambda: (1,))
            return Mock(fetchone=lambda: (0,))

    item = Column("PRICES", "VOLUME", "DOUBLE", "DECIMAL(38,0)")
    scan = preflight_column(Connection(), item)
    assert scan["unsafe_float_integer_count"] == 1
    assert not audit_payload([item], [scan], [])["safe_to_convert"]
