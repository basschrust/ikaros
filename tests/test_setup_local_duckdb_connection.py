"""CLI regressions for unavailable local DuckDB connections."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from config import config as config_module
from database.repositories import factory
from run import setup_local_duckdb


def test_locked_database_exits_without_traceback(
    *, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        config_module, "load_config", lambda _name: {"database": {"driver": "duckdb"}}
    )
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    def fail_connection(_config: dict[str, object]) -> None:
        raise duckdb.IOException(
            'IO Error: Cannot open file "C:\\data\\ikaros.duckdb"\n'
            "File is already open in\n"
            "C:\\Program Files\\DBeaver\\dbeaver.exe (PID 30840)"
        )

    monkeypatch.setattr(factory, "repository_from_config", fail_connection)

    with pytest.raises(SystemExit) as exit_info:
        setup_local_duckdb.main(["--write"])

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "database is locked" in captured.err
    assert "DBeaver" in captured.err
    assert "PID 30840" in captured.err
    assert "Traceback" not in captured.err


def test_other_duckdb_ioerror_exits_without_traceback(
    *, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        config_module, "load_config", lambda _name: {"database": {"driver": "duckdb"}}
    )
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    def fail_connection(_config: dict[str, object]) -> None:
        raise duckdb.IOException("IO Error: Permission denied")

    monkeypatch.setattr(factory, "repository_from_config", fail_connection)

    with pytest.raises(SystemExit) as exit_info:
        setup_local_duckdb.main(["--write"])

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "Cannot open Ikaros DuckDB" in captured.err
    assert "Permission denied" in captured.err
    assert "Traceback" not in captured.err
