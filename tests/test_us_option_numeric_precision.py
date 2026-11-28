"""Fixed-point option/derivative declarations with optional adapter smoke tests."""

import ast
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def declared_ddl(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(assignment.value)
        for assignment in tree.body
        if isinstance(assignment, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "DDL" for t in assignment.targets)
    ]
    assert len(values) == 1
    output = values[0]
    return (output,) if isinstance(output, str) else tuple(output)


def test_ddl_for_option_snapshots_and_derivatives_is_fixed_point() -> None:
    option_sql = declared_ddl(ROOT / "run" / "collect_us_option_chains.py")[0]
    derivative_sql = declared_ddl(ROOT / "run" / "ingest_gpw_derivatives.py")
    assert " FLOAT" not in option_sql.upper()
    assert not any(" FLOAT" in statement.upper() for statement in derivative_sql)
    assert '"IMPLIED_VOLATILITY" NUMBER(24,12)' in option_sql
    assert '"STRIKE" NUMBER(24,8)' in option_sql
    assert '"VOLUME" NUMBER(38,0)' in option_sql
    assert any('"MULTIPLIER" NUMBER(18,4)' in ddl for ddl in derivative_sql)
    assert any('"OPEN_INTEREST" NUMBER(38,0)' in ddl for ddl in derivative_sql)


def test_option_values_quantized_before_persisting() -> None:
    pytest.importorskip("snowflake.connector")
    from run.collect_us_option_chains import optional_number

    assert optional_number("2.123456789", "lastPrice") == Decimal("2.12345679")
    assert optional_number("0.12345678901234", "impliedVolatility") == Decimal("0.123456789012")
    assert optional_number("123", "volume") == Decimal(123)
    with pytest.raises(ValueError, match="integral volume"):
        optional_number("1.5", "volume")
    with pytest.raises(ValueError, match="Invalid bid"):
        optional_number("-0.1", "bid")
