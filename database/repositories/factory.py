"""Repository factory with a candidate-first authoritative-write barrier."""

from pathlib import Path
from typing import Any
from .interface import RepositoryInterface
from database.final_write_guard import protect_repository


def create_repository(
    *, driver: str = "duckdb", output_dir: Path | str = "output", **config: Any
) -> RepositoryInterface:
    driver = driver.lower()
    if driver == "duckdb":
        from database.duckdb_storage import Repository

        repository = Repository(output_dir=output_dir)
    elif driver == "snowflake":
        from database.snowflake_storage import SnowflakeRepository

        repository = SnowflakeRepository(**config)
    else:
        raise ValueError(f"Unsupported database driver: {driver!r}")
    return protect_repository(repository)


def repository_from_config(config: dict[str, Any]) -> RepositoryInterface:
    settings = config.get("database", {})
    driver = settings.get("driver", "duckdb").lower()
    params = settings.get(driver, {})
    if not isinstance(params, dict):
        raise ValueError(f"database.{driver} must be a mapping")
    return create_repository(driver=driver, **params)
