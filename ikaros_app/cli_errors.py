"""Friendly diagnostics for uncaught Ikaros database connection failures.

Register once using install(); unrecognized exceptions retain their normal
tracebacks. No optional database driver is imported during startup.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from types import TracebackType
from typing import Callable

_TRACEBACK_VALUES = frozenset({"1", "true", "yes", "on"})
_DUCKDB_OPEN = re.compile(
    r"cannot open file|could not set lock on file|failed to open file|"
    r"could not open file|database is locked|file is already open in|"
    r"the process cannot access the file because it is being used by another process",
    re.IGNORECASE,
)
_DUCKDB_LOCK = re.compile(
    r"file is already open in|could not set lock on file|database is locked|"
    r"being used by another process|conflicting lock",
    re.IGNORECASE,
)
_FILE = re.compile(
    r"(?:cannot open file|could not set lock on file|failed to open file|"
    r'could not open file)\s+["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OWNER = re.compile(r"file is already open in\s*(.+)", re.IGNORECASE | re.DOTALL)
_SNOWFLAKE_CONNECT = re.compile(
    r"failed to (?:connect|login)|could not connect|connection (?:timed out|refused)|"
    r"no such host is known|network is unreachable|could not resolve host|"
    r"cannot connect|incorrect username or password",
    re.IGNORECASE,
)


def _debug_traceback() -> bool:
    return os.environ.get("IKAROS_TRACEBACK", "").strip().lower() in _TRACEBACK_VALUES


def _chain(exc: BaseException) -> list[BaseException]:
    """Inspect explicit wrappers without accidentally looping over exception cycles."""
    found: list[BaseException] = []
    stack = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if current.__context__ is not None:
            stack.append(current.__context__)
        if current.__cause__ is not None:
            stack.append(current.__cause__)
    return found


def _type_in_module(exc: BaseException, *, prefix: tuple[str, ...], names: set[str]) -> bool:
    return any(
        cls.__module__.startswith(prefix) and cls.__name__ in names for cls in type(exc).__mro__
    )


def format_database_failure(exc: BaseException) -> str | None:
    """Return a short actionable message only for recognized connection errors.

    Other failures (SQL mistakes, programming errors, data validation) deliberately
    return None so the original traceback can show exactly what went wrong.
    """
    for current in _chain(exc):
        details = str(current).strip()
        if _type_in_module(
            current,
            prefix=("_duckdb", "duckdb"),
            names={"IOException", "OperationalError"},
        ) and _DUCKDB_OPEN.search(details):
            path_match = _FILE.search(details)
            path = path_match.group(1).strip() if path_match else None
            if _DUCKDB_LOCK.search(details):
                lines = ["ERROR: Ikaros DuckDB database is locked by another process."]
                if path:
                    lines.append(f"Database: {path}")
                owner = _OWNER.search(details)
                if owner:
                    name = owner.group(1).strip().splitlines()[0].strip()
                    if name:
                        lines.append(f"Lock owner: {name}")
                lines.append(
                    "Close the database connection in DBeaver (or the other process), then retry."
                )
                return "\n".join(lines)
            lines = ["ERROR: Cannot open the Ikaros DuckDB database."]
            if path:
                lines.append(f"Database: {path}")
            lines.extend(
                [
                    f"DuckDB details: {details}",
                    "Check the database path, permissions and available disk space, then retry.",
                ]
            )
            return "\n".join(lines)
        if _type_in_module(
            current,
            prefix=("snowflake.connector",),
            names={"Error", "DatabaseError", "OperationalError", "ProgrammingError"},
        ):
            sqlstate = str(getattr(current, "sqlstate", "") or "")
            errno = getattr(current, "errno", None)
            if sqlstate.startswith("08") or errno == 250001 or _SNOWFLAKE_CONNECT.search(details):
                return (
                    "ERROR: Cannot connect to Snowflake.\n"
                    f"Connector details: {details}\n"
                    "Check the network, account URL and credentials, then retry."
                )
    return None


def install() -> None:
    """Install idempotent handlers for uncaught main-thread/thread exceptions."""
    if not getattr(sys.excepthook, "_ikaros_friendly_db_errors", False):
        previous: Callable[[type[BaseException], BaseException, TracebackType | None], None] = (
            sys.excepthook
        )

        def friendly_excepthook(
            exc_type: type[BaseException],
            exc_value: BaseException,
            traceback: TracebackType | None,
        ) -> None:
            if not _debug_traceback():
                try:
                    message = format_database_failure(exc_value)
                except Exception:
                    message = None
                if message is not None:
                    print(message, file=sys.stderr)
                    return
            previous(exc_type, exc_value, traceback)

        setattr(friendly_excepthook, "_ikaros_friendly_db_errors", True)
        sys.excepthook = friendly_excepthook

    if not getattr(threading.excepthook, "_ikaros_friendly_db_errors", False):
        previous_thread_hook = threading.excepthook

        def friendly_thread_excepthook(args: threading.ExceptHookArgs) -> None:
            if not _debug_traceback() and args.exc_value is not None:
                try:
                    message = format_database_failure(args.exc_value)
                except Exception:
                    message = None
                if message is not None:
                    print(message, file=sys.stderr)
                    return
            previous_thread_hook(args)

        setattr(friendly_thread_excepthook, "_ikaros_friendly_db_errors", True)
        threading.excepthook = friendly_thread_excepthook
