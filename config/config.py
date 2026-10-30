"""Ikaros YAML configuration and optional repository-local .env loading."""

from pathlib import Path

from dotenv import load_dotenv
import yaml

DEFAULT_CONFIG = "instruments.yaml"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_project_env() -> None:
    """Load secrets from project root; explicit environment variables win."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


# Most existing ingestion modules import load_config before opening Snowflake.
# Loading at import time preserves those entry points unchanged.
load_project_env()


class IncludeLoader(yaml.SafeLoader):
    pass


def include_constructor(loader, node):
    relative_path = loader.construct_scalar(node)
    base_path = Path(loader.name).parent
    file_path = base_path / relative_path
    with open(file_path, "r") as f:
        sub_loader = IncludeLoader(f)
        sub_loader.name = str(file_path)
        return sub_loader.get_single_data()


IncludeLoader.add_constructor("!include", include_constructor)


def load_config(filename: str = DEFAULT_CONFIG) -> dict:
    src_file = PROJECT_ROOT / "config" / filename
    with open(src_file, "r") as f:
        loader = IncludeLoader(f)
        loader.name = str(src_file)
        return loader.get_single_data()
