"""Database boundary: audit and explicitly migrate persisted financial numeric types in DuckDB.

Examples (from Ikaros project root, with the Ikaros environment active):

  python -m run.numeric_storage --database "C:/projects_AK/Daedalus/input/market_data/ikaros.duckdb"
  python -m run.numeric_storage --database ".../ikaros.duckdb" --write

The default is READ-ONLY. A write takes a checkpoint, creates a full backup
while holding the shared Ikaros/Daedalus lock, checks every conversion, and
runs all type changes in one DuckDB transaction. It never uses the repository
constructor, which could mutate the database before the preflight.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any

from database.numeric_contract import Column, classify, decimal_parts, floating_type


class UnsafeNumericMigration(RuntimeError):
    """The existing numeric values/unknown schema need explicit intervention."""


def list_columns(connection: Any) -> list[Column]:
    records = connection.execute(
        """SELECT c.table_name, c.column_name, c.data_type
           FROM information_schema.columns c
           JOIN information_schema.tables t
           ON t.table_catalog=c.table_catalog
           AND t.table_schema=c.table_schema AND t.table_name=c.table_name
           WHERE c.table_schema=current_schema() AND t.table_type='BASE TABLE'
           ORDER BY c.table_name, c.ordinal_position"""
    ).fetchall()
    return classify([(str(t), str(c), str(d)) for t, c, d in records])


def _ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or not value[0].isalpha():
        raise ValueError(f"Invalid column/table name from catalog: {value!r}")
    return f'"{value}"'


def _count(connection: Any, query: str) -> int:
    return int(connection.execute(query).fetchone()[0])


def _cast_expression(item: Column) -> str:
    assert item.target is not None
    _, scale = decimal_parts(item.target)
    identifier = _ident(item.name)
    return f"CAST(ROUND({identifier}, {scale}) AS {item.target})"


def preflight_column(connection: Any, item: Column) -> dict[str, int | str]:
    """Check every original non-null value without changing the table."""
    if not item.needs_conversion or not item.target:
        raise ValueError("Preflight requires a known column in need of conversion")
    table, name = _ident(item.table), _ident(item.name)
    precision, scale = decimal_parts(item.target)
    # TRY_CAST also detects out-of-range values before ALTER. Count bad rows,
    # rather than permitting an implicit conversion to null or an overflow.
    bad = _count(
        connection,
        f"SELECT COUNT(*) FROM {table} WHERE {name} IS NOT NULL "
        f"AND (NOT isfinite(CAST({name} AS DOUBLE)) OR "
        f"TRY_CAST(ROUND({name}, {scale}) AS {item.target}) IS NULL)",
    )
    fractional = 0
    precision_risk = 0
    if scale == 0:
        fractional = _count(
            connection,
            f"SELECT COUNT(*) FROM {table} WHERE {name} IS NOT NULL "
            f"AND isfinite(CAST({name} AS DOUBLE)) AND {name} <> TRUNC({name})",
        )
        # Integer values previously held in FLOAT are untrustworthy beyond the
        # exactly representable IEEE-754 integer range; never claim losslessness.
        if item.float_stored:
            precision_risk = _count(
                connection,
                f"SELECT COUNT(*) FROM {table} WHERE {name} IS NOT NULL "
                f"AND ABS({name}) > 9007199254740991",
            )
    return {
        "table": item.table,
        "column": item.name,
        "from": item.actual,
        "to": item.target,
        "precision": precision,
        "scale": scale,
        "non_null": _count(connection, f"SELECT COUNT({name}) FROM {table}"),
        "invalid_or_overflow": bad,
        "fractional_count": fractional,
        "unsafe_float_integer_count": precision_risk,
    }


def preflight(connection: Any) -> tuple[list[Column], list[dict[str, int | str]], list[Column]]:
    columns = list_columns(connection)
    unknown = [item for item in columns if item.unknown_float or item.unknown_storage]
    proposed = [item for item in columns if item.needs_conversion]
    scans = [preflight_column(connection, item) for item in proposed]
    return columns, scans, unknown


def audit_payload(
    columns: list[Column], scans: list[dict[str, int | str]], unknown: list[Column]
) -> dict[str, Any]:
    return {
        "policy": "Ikaros fixed-point numeric storage, v1",
        "floating_columns": [
            {"table": c.table, "column": c.name, "actual": c.actual, "target": c.target}
            for c in columns
            if floating_type(c.actual)
        ],
        "unclassified_or_non_numeric_columns": [
            {"table": c.table, "column": c.name, "actual": c.actual} for c in unknown
        ],
        "planned_conversions": scans,
        "safe_to_convert": not unknown
        and all(
            all(
                row[key] == 0
                for key in ("invalid_or_overflow", "fractional_count", "unsafe_float_integer_count")
            )
            for row in scans
        ),
    }


def _print_summary(payload: dict[str, Any]) -> None:
    floats = payload["floating_columns"]
    print(f"Floating-point columns detected: {len(floats)}")
    for item in floats:
        label = f"-> {item['target']}" if item["target"] else "-> UNKNOWN / BLOCKED"
        print(f"  {item['table']}.{item['column']}: {item['actual']} {label}")
    for item in payload["unclassified_or_non_numeric_columns"]:
        print(
            f"  BLOCKED {item['table']}.{item['column']}: "
            f"{item['actual']} has no safe conversion policy"
        )
    print(f"Conversions planned: {len(payload['planned_conversions'])}")
    for row in payload["planned_conversions"]:
        bad = (
            row["invalid_or_overflow"] + row["fractional_count"] + row["unsafe_float_integer_count"]
        )
        print(
            f"  {row['table']}.{row['column']}: {row['from']} -> {row['to']} "
            f"non_null={row['non_null']} unsafe={bad}"
        )
    print(
        "SAFE TO MIGRATE"
        if payload["safe_to_convert"]
        else "MIGRATION BLOCKED: investigate the audit"
    )


def _backup_path(database: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return database.with_name(f"{database.stem}.before_decimal_{timestamp}.duckdb")


def inspect(database: Path, *, write: bool = False, report: Path | None = None) -> int:
    import duckdb
    from database.duckdb_file_lock import duckdb_access_lock

    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    database = database.resolve()
    # Ikaros and Daedalus use the same OS-held lock-file convention. DuckDB
    # itself also enforces exclusion against uncooperative clients (DBeaver).
    with duckdb_access_lock(database_path=database):
        with closing(duckdb.connect(str(database), read_only=not write)) as con:
            columns, scans, unknown = preflight(con)
            payload = audit_payload(columns, scans, unknown)
            payload["database"] = str(database)
            _print_summary(payload)
            if report is not None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                print(f"Audit saved to {report}")
            if not write:
                return 0 if payload["safe_to_convert"] else 2
            if not payload["safe_to_convert"]:
                raise UnsafeNumericMigration(
                    "Conversion rejected. Resolve unknown float columns, non-finite/out-of-range "
                    "numbers, fractional volume or unsafe floating integers first."
                )
            if not scans:
                print("Already compliant for all recognized columns; no backup necessary")
                return 0
            before_counts = {
                table: _count(con, f"SELECT COUNT(*) FROM {_ident(table)}")
                for table in sorted({c.table for c in columns if c.needs_conversion})
            }
            # Create an on-disk, checkpointed copy before any DDL. Keep the
            # application mutex held while the original is closed and copied.
            con.execute("CHECKPOINT")
        backup = _backup_path(database)
        if backup.exists():
            raise FileExistsError(f"Backup already exists: {backup}")
        shutil.copy2(database, backup)
        print(f"BACKUP (retain this file): {backup}")
        with closing(duckdb.connect(str(database))) as con:
            con.execute("BEGIN TRANSACTION")
            try:
                for item in [c for c in columns if c.needs_conversion]:
                    assert item.target is not None
                    statement = (
                        f"ALTER TABLE {_ident(item.table)} ALTER COLUMN {_ident(item.name)} "
                        f"TYPE {item.target} USING {_cast_expression(item)}"
                    )
                    con.execute(statement)
                after = {(c.table, c.name): c for c in list_columns(con)}
                for item in [c for c in columns if c.needs_conversion]:
                    assert item.target is not None
                    actual = after[(item.table, item.name)].actual
                    from database.numeric_contract import normalized_type

                    if normalized_type(actual) != normalized_type(item.target):
                        raise UnsafeNumericMigration(
                            f"Type mismatch after conversion {item.table}.{item.name}: {actual}"
                        )
                for table, before_count in before_counts.items():
                    after_count = _count(con, f"SELECT COUNT(*) FROM {_ident(table)}")
                    if after_count != before_count:
                        raise UnsafeNumericMigration(
                            f"Row count changed in {table}: {before_count} -> {after_count}"
                        )
                for row in scans:
                    table, name = _ident(str(row["table"])), _ident(str(row["column"]))
                    after_non_null = _count(con, f"SELECT COUNT({name}) FROM {table}")
                    if after_non_null != row["non_null"]:
                        raise UnsafeNumericMigration(
                            f"NULL count changed in {row['table']}.{row['column']}"
                        )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                print(f"ROLLED BACK. Original copy remains at {backup}")
                raise
        print("COMPLETE: migrated recognized financial columns; all DDL committed")
        print("Re-run this command without --write to verify the final schema")
        return 0
