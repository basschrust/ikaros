"""Local-vs-cloud factory choices and legacy SQL conversion without credentials."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from database.duckdb_compat import duckdb_sql
from database.repositories.factory import repository_from_config


def test_sql_rewrite_for_legacy_equity_candidate_insertion() -> None:
    sql = (
        'INSERT INTO "PRICES_CANDIDATES" VALUES '
        "(\"PRICES_CANDIDATES_ID_SEQ\".NEXTVAL,%s,TO_DATE(%s,'YYYY-MM-DD'),"
        "TO_DECIMAL(%s,24,8),TO_TIMESTAMP_NTZ(%s,'YYYY-MM-DD'))"
    )
    result = duckdb_sql(sql)
    assert "nextval('PRICES_CANDIDATES_ID_SEQ')" in result
    assert "CAST(? AS DATE)" in result
    assert "CAST(? AS DECIMAL(24,8))" in result
    assert "CAST(? AS TIMESTAMP)" in result
    assert "%s" not in result


def test_sql_rewrite_for_inventory() -> None:
    assert duckdb_sql('SELECT MAX(TO_DATE("DATE")) FROM "PRICES" WHERE "VENUE"=%s') == (
        'SELECT MAX(CAST("DATE" AS DATE)) FROM "PRICES" WHERE "VENUE"=?'
    )


def test_explicit_duckdb_directory_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.repositories.factory as factory

    calls: list[Path] = []

    class Dummy:
        def __init__(self, *, output_dir: Path) -> None:
            calls.append(output_dir)
            self.connection = object()

    import database.local_duckdb as local

    monkeypatch.setattr(local, "LocalDuckDBRepository", Dummy)
    monkeypatch.setattr(factory, "protect_repository", lambda repo: repo)
    monkeypatch.setenv("IKAROS_MARKET_DATA_DIR", str(tmp_path / "different"))
    assert factory.create_repository(driver="duckdb", output_dir=tmp_path).connection is not None
    assert calls == [tmp_path]


def test_env_directory_applies_when_not_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.repositories.factory as factory
    import database.local_duckdb as local

    calls: list[Path] = []
    monkeypatch.setattr(
        local,
        "LocalDuckDBRepository",
        lambda *, output_dir: calls.append(output_dir) or SimpleNamespace(connection=object()),
    )
    monkeypatch.setattr(factory, "protect_repository", lambda repo: repo)
    monkeypatch.setenv("IKAROS_MARKET_DATA_DIR", str(tmp_path / "market_data"))
    factory.create_repository(driver="duckdb")
    assert calls == [tmp_path / "market_data"]


def test_duckdb_choice_never_constructs_snowflake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database.repositories.factory as factory
    import database.local_duckdb as local

    monkeypatch.setattr(
        local,
        "LocalDuckDBRepository",
        lambda *, output_dir: SimpleNamespace(connection=object()),
    )
    monkeypatch.setattr(factory, "protect_repository", lambda repo: repo)
    result = repository_from_config(
        {"database": {"driver": "duckdb", "duckdb": {"output_dir": str(tmp_path)}}}
    )
    assert result.connection is not None


def test_missing_local_dir_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from database.repositories.factory import create_repository

    monkeypatch.delenv("IKAROS_MARKET_DATA_DIR", raising=False)
    with pytest.raises(ValueError, match="IKAROS_MARKET_DATA_DIR"):
        create_repository(driver="duckdb")
