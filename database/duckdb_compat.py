"""Translate the small Snowflake SQL subset used by Ikaros's legacy equity pipeline.

Other database modules use native DuckDB bindings. SQL translation stays inside
`database/`; this adapter never connects to Snowflake.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


_TO_DATE_FORMAT = re.compile(
    r"\bTO_DATE\(\s*(%s|\?|(?:\"[^\"]+\"|[\w.]+))\s*,\s*'YYYY-MM-DD'\s*\)",
    re.IGNORECASE,
)
_TO_TIMESTAMP_FORMAT = re.compile(
    r"\bTO_TIMESTAMP_NTZ\(\s*(%s|\?|(?:\"[^\"]+\"|[\w.]+))\s*,\s*'YYYY-MM-DD'\s*\)",
    re.IGNORECASE,
)
_TO_DECIMAL = re.compile(
    r"\bTO_DECIMAL\(\s*(%s|\?)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)
_TO_DATE = re.compile(r"\bTO_DATE\(\s*([^()]+?)\s*\)", re.IGNORECASE)
_TIMESTAMP = re.compile(r"\bTO_TIMESTAMP_NTZ\(\s*([^()]+?)\s*\)", re.IGNORECASE)
_NEXTVAL = re.compile(r'"([A-Za-z_][\w]*)"\.NEXTVAL\b', re.IGNORECASE)


def duckdb_sql(sql: str) -> str:
    """Convert parameter markers and scalar conversions; do not parse arbitrary SQL."""
    statement = _TO_DATE_FORMAT.sub(r"CAST(\1 AS DATE)", sql)
    statement = _TO_TIMESTAMP_FORMAT.sub(r"CAST(\1 AS TIMESTAMP)", statement)
    statement = _TO_DECIMAL.sub(r"CAST(\1 AS DECIMAL(\2,\3))", statement)
    statement = _TO_DATE.sub(r"CAST(\1 AS DATE)", statement)
    statement = _TIMESTAMP.sub(r"CAST(\1 AS TIMESTAMP)", statement)
    statement = _NEXTVAL.sub(lambda m: f"nextval('{m.group(1)}')", statement)
    statement = statement.replace("%s", "?")
    return statement


class CompatibleCursor:
    """DB-API cursor facade preserving logical row counts for DuckDB DML.

    All cursors intentionally share the same raw DuckDB connection/transaction.
    DuckDB.cursor() creates a duplicate connection, which would break the nested
    BEGIN -> persist_candidates() -> COMMIT workflow.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.rowcount = -1
        self._saved_rows: list[tuple[Any, ...]] | None = None

    def execute(self, command: str, params: Iterable[Any] = ()) -> CompatibleCursor:
        statement = duckdb_sql(command)
        self._inner.execute(statement, tuple(params))
        self._saved_rows = None
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            # DuckDB returns a one-row Count result for DML, whereas its Python
            # cursor.rowcount remains -1 (the legacy Snowflake code expects 1).
            rows = self._inner.fetchall()
            self._saved_rows = list(rows)
            if len(rows) == 1 and len(rows[0]) == 1:
                self.rowcount = int(rows[0][0])
            else:
                self.rowcount = -1
        else:
            self.rowcount = -1
        return self

    def executemany(self, command: str, params: Iterable[Iterable[Any]]) -> CompatibleCursor:
        self._saved_rows = None
        self._inner.executemany(duckdb_sql(command), list(params))
        self.rowcount = -1
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self._saved_rows is not None:
            rows, self._saved_rows = self._saved_rows, None
            return rows
        return list(self._inner.fetchall())

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._saved_rows is not None:
            if not self._saved_rows:
                self._saved_rows = None
                return None
            return self._saved_rows.pop(0)
        return self._inner.fetchone()

    def close(self) -> None:
        # Logical cursor closes must not close the shared connection.
        return None

    def __enter__(self) -> CompatibleCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class CompatibleConnection:
    """DuckDB connection facade for Ikaros SQL modules and repository methods."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._result: CompatibleCursor | None = None

    def cursor(self, *args: Any, **kwargs: Any) -> CompatibleCursor:
        if args or kwargs:
            raise TypeError("DuckDB cursor does not accept cursor options")
        return CompatibleCursor(self._connection)

    def execute(self, command: str, params: Iterable[Any] = ()) -> CompatibleConnection:
        # DuckDB's connection.execute returns itself; support fetchall chaining.
        if self._result is not None:
            self._result.close()
        self._result = self.cursor().execute(command, params)
        return self

    def executemany(self, command: str, params: Iterable[Iterable[Any]]) -> CompatibleConnection:
        if self._result is not None:
            self._result.close()
        self._result = self.cursor().executemany(command, params)
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self._result is None:
            raise RuntimeError("No current query result")
        return self._result.fetchall()

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._result is None:
            raise RuntimeError("No current query result")
        return self._result.fetchone()

    def fetchdf(self) -> Any:
        if self._result is None:
            raise RuntimeError("No current query result")
        return self._result.fetchdf()

    def close(self) -> None:
        if self._result is not None:
            self._result.close()
            self._result = None
        self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)
