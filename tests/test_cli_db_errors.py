"""Regression coverage for the shared Ikaros terminal error formatter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ikaros_app.cli_errors import format_database_failure, install


class IOException(Exception):
    __module__ = "_duckdb"


class DatabaseError(Exception):
    __module__ = "snowflake.connector.errors"

    def __init__(self, message: str, *, errno: int | None = None, sqlstate: str | None = None):
        super().__init__(message)
        self.errno = errno
        self.sqlstate = sqlstate


def locked_db() -> IOException:
    return IOException(
        'IO Error: Cannot open file "C:\\market_data\\ikaros.duckdb": '
        "The process cannot access the file because it is being used "
        "by another process.\n\n"
        "File is already open in\nC:\\DBeaver\\dbeaver.exe (PID 30840)"
    )


def test_lock_message_has_action_and_process() -> None:
    msg = format_database_failure(locked_db())
    assert msg is not None
    assert "database is locked" in msg
    assert "ikaros.duckdb" in msg
    assert "DBeaver" in msg
    assert "PID 30840" in msg
    assert "Close the database connection" in msg
    assert "Traceback" not in msg


def test_open_file_permission_error_is_friendly() -> None:
    msg = format_database_failure(
        IOException('IO Error: Cannot open file "C:\\ikaros.duckdb": Access denied')
    )
    assert msg is not None
    assert "Cannot open" in msg
    assert "Access denied" in msg


def test_sql_errors_not_swallowed() -> None:
    assert format_database_failure(ValueError("bad data")) is None
    assert format_database_failure(IOException("IO Error: SQL table is missing")) is None


def test_wrapped_database_failure() -> None:
    try:
        try:
            raise locked_db()
        except IOException as exc:
            raise RuntimeError("unable to start") from exc
    except RuntimeError as wrapped:
        msg = format_database_failure(wrapped)
        assert msg is not None
        assert "PID 30840" in msg


def test_snowflake_connect_but_not_sql_syntax() -> None:
    assert "Cannot connect to Snowflake" in (
        format_database_failure(DatabaseError("Login failed: No such host is known", errno=250001))
        or ""
    )
    assert format_database_failure(DatabaseError("SQL compilation error", sqlstate="42000")) is None


def test_idempotent_install() -> None:
    import threading

    before = sys.excepthook
    before_thread = threading.excepthook
    try:
        install()
        once = sys.excepthook
        once_thread = threading.excepthook
        install()
        assert sys.excepthook is once
        assert threading.excepthook is once_thread
    finally:
        sys.excepthook = before
        threading.excepthook = before_thread


def test_main_thread_hook_real_subprocess(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("IKAROS_TRACEBACK", None)
    env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import run\n"
        "from types import ModuleType\n"
        "import sys\n"
        "m=ModuleType('_duckdb')\n"
        "E=type('IOException',(Exception,),{'__module__':'_duckdb'})\n"
        "m.IOException=E\n"
        "sys.modules['_duckdb']=m\n"
        'raise E(\'IO Error: Cannot open file \\"C:/ikaros.duckdb\\"; '
        "File is already open in DBeaver.exe (PID 40)')\n"
    )
    p = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert p.returncode == 1
    assert "database is locked" in p.stderr
    assert "DBeaver.exe (PID 40)" in p.stderr
    assert "Traceback" not in p.stderr


def test_debug_switch_restores_traceback(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["IKAROS_TRACEBACK"] = "1"
    code = (
        "import run\n"
        "E=type('IOException',(Exception,),{'__module__':'_duckdb'})\n"
        "raise E('IO Error: Cannot open file locked, "
        "file is already open in DBeaver.exe (PID 40)')"
    )
    p = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert p.returncode == 1
    assert "Traceback" in p.stderr
    assert "IOException" in p.stderr
