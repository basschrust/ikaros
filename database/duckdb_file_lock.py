"""Interprocess access coordination for a local DuckDB database file.

This advisory lock applies to *cooperating Ikaros/Daedalus processes*.
It is not the DuckDB file lock and cannot stop tools such as DBeaver.
No external dependency: msvcrt on Windows, fcntl on POSIX.
"""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DuckDBAccessTimeout(TimeoutError):
    """The DuckDB hand-off mutex was held for too long."""


def access_lock_path(*, database_path: Path | str) -> Path:
    """The same resolved database path must produce the same lock-file name."""
    source = Path(database_path).expanduser().resolve()
    return source.with_name(source.name + ".access.lock")


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def duckdb_access_lock(
    *,
    database_path: Path | str,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 0.1,
) -> Iterator[None]:
    """Hold an OS mutex until after DuckDB has been completely closed.

    Never unlink the lock file: removing it can create two different mutexes
    with the same pathname while another process still holds the old inode.
    """
    if timeout_seconds < 0 or poll_seconds <= 0:
        raise ValueError("timeout_seconds must be >= 0 and poll_seconds > 0")
    lock_file = access_lock_path(database_path=database_path)
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
    acquired = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if _try_lock(fd):
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise DuckDBAccessTimeout(
                    f"Timed out waiting for Ikaros/Daedalus database access lock: {lock_file}. "
                    "Another coordinated process still has the database open."
                )
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if acquired:
                _unlock(fd)
        finally:
            os.close(fd)
