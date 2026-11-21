"""Backend-selecting repository factory; .env overrides YAML when provided."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .interface import RepositoryInterface
from database.final_write_guard import protect_repository


def create_repository(
    *, driver: str = "duckdb", output_dir: Path | str | None = None, **config: Any
) -> RepositoryInterface:
    driver = driver.lower()
    if driver == "duckdb":
        from database.local_duckdb import LocalDuckDBRepository

        location = output_dir if output_dir is not None else os.getenv("IKAROS_MARKET_DATA_DIR")
        if not location:
            raise ValueError(
                "DuckDB selected: set IKAROS_MARKET_DATA_DIR in .env to a directory "
                "(or database.duckdb.output_dir in YAML)"
            )
        repository = LocalDuckDBRepository(output_dir=Path(location).expanduser())
    elif driver == "snowflake":
        from database.authenticated_snowflake import AuthenticatedSnowflakeRepository

        repository = AuthenticatedSnowflakeRepository(**config)
    else:
        raise ValueError(f"Unsupported database driver: {driver!r}")
    return protect_repository(repository)


def repository_from_config(config: dict[str, Any]) -> RepositoryInterface:
    from config.config import load_project_env

    load_project_env()
    settings = config.get("database", {})
    driver = settings.get("driver", "duckdb").lower()
    params = settings.get(driver, {})
    if not isinstance(params, dict):
        raise ValueError(f"database.{driver} must be a mapping")
    return create_repository(driver=driver, **params)
