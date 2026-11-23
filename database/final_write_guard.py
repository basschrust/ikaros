"""Fail-closed barrier against bypassing candidate-based price promotion.

This guards connections returned by Ikaros's repository factory; it is a
code-level safety boundary, NOT a substitute for independent database roles.
The only authorized authoritative-price DML originates from the independent
candidate promotion modules. Candidate staging and reference data are allowed.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

AUTHORITATIVE_TABLES = frozenset(
    {
        "PRICES",
        "FX_RATES",
        "INDEX_PRICES",
        "DERIVATIVE_PRICES",
        "RATE_OBSERVATIONS",
    }
)
_ALLOWED_PROMOTERS = frozenset(
    {
        ("database.candidate_promotion", "promote_dataset"),
        ("database.candidate_promotion", "promote_equities"),
        ("database.candidate_promotion", "promote_market_prices"),
        ("database.candidate_promotion", "_execute_promotions"),
        ("database.candidate_observations", "promote_stored"),
        ("database.candidate_observations", "_promote_fx"),
        ("database.candidate_observations", "_promote_rate"),
    }
)
# Identify a SQL DML or destructive DDL target, optionally database/schema qualified.
_IDENTIFIER = r'(?:"[A-Za-z_][\w$]*"|[A-Za-z_][\w$]*)'
_TARGET = rf"(?P<table>{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER}){{0,2}})"
_TARGET_WRITES = re.compile(
    rf"\b(?:INSERT\s+(?:OVERWRITE\s+)?INTO|MERGE\s+INTO|COPY\s+INTO|UPDATE|DELETE\s+FROM|"
    rf"TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?!IF\s+NOT\s+EXISTS))\s+{_TARGET}",
    re.IGNORECASE,
)


class CandidateBoundaryError(RuntimeError):
    """Attempt to publish a price without using candidate promotion."""


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments without executing them (guard is a policy check)."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _write_targets(sql: str) -> set[str]:
    targets: set[str] = set()
    for match in _TARGET_WRITES.finditer(_strip_sql_comments(sql)):
        identifier = match.group("table").split(".")[-1].strip().strip('"').upper()
        if identifier in AUTHORITATIVE_TABLES:
            targets.add(identifier)
    return targets


def _within_promotion() -> bool:
    """Check the call origin; deliberately not a security/authentication claim."""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if (frame.f_globals.get("__name__"), frame.f_code.co_name) in _ALLOWED_PROMOTERS:
                return True
            frame = frame.f_back
        return False
    finally:
        del frame


def require_candidate_promotion(sql: str) -> None:
    targets = _write_targets(sql)
    if targets and not _within_promotion():
        names = ", ".join(sorted(targets))
        raise CandidateBoundaryError(
            f"Direct authoritative price write blocked ({names}). "
            "Stage observations with run.stage_candidates; publish them only "
            "with run.promote_candidates. Old direct-write commands are disabled."
        )


class GuardedCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, command: str, params: Any = (), **kwargs: Any) -> Any:
        require_candidate_promotion(command)
        result = self._cursor.execute(command, params, **kwargs)
        return self if result is self._cursor else result

    def executemany(self, command: str, params: Any, **kwargs: Any) -> Any:
        require_candidate_promotion(command)
        result = self._cursor.executemany(command, params, **kwargs)
        return self if result is self._cursor else result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __enter__(self) -> GuardedCursor:
        if hasattr(self._cursor, "__enter__"):
            self._cursor.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if hasattr(self._cursor, "__exit__"):
            return self._cursor.__exit__(exc_type, exc, tb)
        self._cursor.close()
        return None


class GuardedConnection:
    """Minimal DB-API-compatible facade with pass-through for other methods."""

    def __init__(self, connection: Any):
        self._connection = connection

    def cursor(self, *args: Any, **kwargs: Any) -> GuardedCursor:
        return GuardedCursor(self._connection.cursor(*args, **kwargs))

    def execute(self, command: str, params: Any = (), **kwargs: Any) -> Any:
        require_candidate_promotion(command)
        result = self._connection.execute(command, params, **kwargs)
        return self if result is self._connection else result

    def executemany(self, command: str, params: Any, **kwargs: Any) -> Any:
        require_candidate_promotion(command)
        result = self._connection.executemany(command, params, **kwargs)
        return self if result is self._connection else result

    def sql(self, command: str, *args: Any, **kwargs: Any) -> Any:
        """Protect DuckDB's convenience SQL execution path too."""
        require_candidate_promotion(command)
        return self._connection.sql(command, *args, **kwargs)

    def query(self, command: str, *args: Any, **kwargs: Any) -> Any:
        require_candidate_promotion(command)
        return self._connection.query(command, *args, **kwargs)

    def __enter__(self) -> GuardedConnection:
        if hasattr(self._connection, "__enter__"):
            self._connection.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if hasattr(self._connection, "__exit__"):
            return self._connection.__exit__(exc_type, exc, tb)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def protect_repository(repo: Any) -> Any:
    """Activate after repository bootstrap, and stop convenience bulk bypasses."""
    if not isinstance(repo.connection, GuardedConnection):
        repo.connection = GuardedConnection(repo.connection)
    # Some repository helpers use Snowflake write_pandas rather than cursor SQL.
    if hasattr(repo, "_insert_frame") and not getattr(repo, "_candidate_bulk_protected", False):
        original = repo._insert_frame

        def checked_insert_frame(table: str, frame: Any) -> None:
            if str(table).upper() in AUTHORITATIVE_TABLES:
                raise CandidateBoundaryError(
                    f"Direct bulk write to {table} blocked; use candidate staging/promotion"
                )
            return original(table, frame)

        repo._insert_frame = checked_insert_frame
        repo._candidate_bulk_protected = True
    return repo
