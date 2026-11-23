"""Ikaros YAML configuration, with optional .env backend selection."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
import yaml
from yaml.nodes import ScalarNode

DEFAULT_CONFIG = "instruments.yaml"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_project_env() -> None:
    """Read project secrets/settings, preserving explicit environment overrides."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


load_project_env()


class IncludeLoader(yaml.SafeLoader):
    pass


def include_constructor(loader: IncludeLoader, node: ScalarNode):
    relative_path = loader.construct_scalar(node)
    file_path = Path(loader.name).parent / relative_path
    with file_path.open(encoding="utf-8") as stream:
        sub_loader = IncludeLoader(stream)
        sub_loader.name = str(file_path)
        return sub_loader.get_single_data()


IncludeLoader.add_constructor("!include", include_constructor)


def load_config(filename: str = DEFAULT_CONFIG) -> dict:
    src_file = PROJECT_ROOT / "config" / filename
    with src_file.open(encoding="utf-8") as stream:
        loader = IncludeLoader(stream)
        loader.name = str(src_file)
        data = loader.get_single_data()
    if filename == "database.yaml":
        # Explicit .env selection always wins over the versioned YAML default.
        env_driver = os.getenv("IKAROS_DB_DRIVER")
        if env_driver:
            data.setdefault("database", {})["driver"] = env_driver.strip().lower()
    return data
