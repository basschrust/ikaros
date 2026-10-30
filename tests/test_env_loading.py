"""No live credentials required; exercise priority and missing-file behavior."""

from pathlib import Path
import os

from config import config


def test_reads_local_env_without_overwriting_explicit_environment(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SNOWFLAKE_ACCOUNT=test-account\nSNOWFLAKE_USER=from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.setenv("SNOWFLAKE_USER", "from-process")
    config.load_project_env()
    assert os.environ["SNOWFLAKE_ACCOUNT"] == "test-account"
    assert os.environ["SNOWFLAKE_USER"] == "from-process"


def test_missing_dotenv_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    config.load_project_env()
    assert "SNOWFLAKE_ACCOUNT" not in os.environ


def test_example_has_no_credentials():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("SNOWFLAKE_"):
            assert line.endswith("=")
