"""Real subprocess tests for the Ikaros/Daedalus database hand-off mutex."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from database.duckdb_file_lock import (
    DuckDBAccessTimeout,
    access_lock_path,
    duckdb_access_lock,
)


def _child(*, db_path: Path, code: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(db_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_other_process_waits_for_current_owner(*, tmp_path: Path) -> None:
    db_path = tmp_path / "ikaros.duckdb"
    code = (
        "import sys, time\n"
        "from database.duckdb_file_lock import duckdb_access_lock\n"
        "path=sys.argv[1]\n"
        "with duckdb_access_lock(database_path=path):\n"
        " print('LOCKED', flush=True)\n"
        " time.sleep(0.7)\n"
    )
    child = _child(db_path=db_path, code=code)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(DuckDBAccessTimeout):
            with duckdb_access_lock(database_path=db_path, timeout_seconds=0.1):
                pytest.fail("Cannot enter while subprocess owns the lock")
        assert child.wait(timeout=5) == 0
        with duckdb_access_lock(database_path=db_path, timeout_seconds=0.1):
            assert access_lock_path(database_path=db_path).is_file()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_os_releases_lock_after_abrupt_process_exit(*, tmp_path: Path) -> None:
    db_path = tmp_path / "ikaros.duckdb"
    code = (
        "import os, sys\n"
        "from database.duckdb_file_lock import duckdb_access_lock\n"
        "with duckdb_access_lock(database_path=sys.argv[1]):\n"
        " print('LOCKED', flush=True)\n"
        " os._exit(7)\n"
    )
    child = _child(db_path=db_path, code=code)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        assert child.wait(timeout=5) == 7
        with duckdb_access_lock(database_path=db_path, timeout_seconds=0.2):
            assert access_lock_path(database_path=db_path).exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_bad_timeout_is_rejected(*, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        with duckdb_access_lock(database_path=tmp_path / "ikaros.duckdb", timeout_seconds=-1):
            pytest.fail("Invalid lock parameters must fail")


def test_repository_holds_lock_until_close(
    *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("duckdb")
    from database.duckdb_storage import Repository

    monkeypatch.setattr(Repository, "prepare_tables", lambda self: None)
    repo = Repository(output_dir=tmp_path)
    try:
        with pytest.raises(DuckDBAccessTimeout):
            with duckdb_access_lock(database_path=repo.localdb, timeout_seconds=0.1):
                pytest.fail("Repository opened an uncoordinated DuckDB session")
    finally:
        repo.close()
    with duckdb_access_lock(database_path=repo.localdb, timeout_seconds=0.1):
        pass


def test_failed_repository_init_releases_lock(
    *, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    duckdb = pytest.importorskip("duckdb")
    from database.duckdb_storage import Repository

    def fail_connect(_db: str) -> None:
        raise duckdb.IOException("Synthetic database-open failure")

    monkeypatch.setattr(duckdb, "connect", fail_connect)
    with pytest.raises(duckdb.IOException, match="Synthetic"):
        Repository(output_dir=tmp_path)
    with duckdb_access_lock(database_path=tmp_path / "ikaros.duckdb", timeout_seconds=0.1):
        pass
