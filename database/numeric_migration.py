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
import re
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
            if any(c.table == "SECURITIES" for c in columns if c.needs_conversion):
                raise UnsafeNumericMigration(
                    "SECURITIES is referenced by PK/FK dependencies. DuckDB cannot ALTER its "
                    "columns safely in place. Use --rebuild --output <NEW.duckdb> instead. "
                    "The original database has not been changed."
                )
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


# --- Rebuild for DuckDB relations protected by primary/foreign-key dependencies. ---
# The old --write path above is preserved for independent tables. The --rebuild
# workflow never executes ALTER TABLE on the original database.

_CREATE_TABLE = re.compile(
    r"\bCREATE\s+TABLE\s+"
    r"(?:(?:\"[^\"]+\"|[A-Za-z_][A-Za-z_0-9]*)\s*\.\s*)?"
    r"(?:\"(?P<quoted>[^\"]+)\"|(?P<bare>[A-Za-z_][A-Za-z_0-9]*))\s*\(",
    flags=re.IGNORECASE,
)


def _type_regex(actual: str) -> str:
    """Match one precise old SQL declaration, allowing whitespace around commas."""
    from database.numeric_contract import normalized_type

    canonical = normalized_type(actual)
    if canonical.startswith("DECIMAL("):
        precision, scale = decimal_parts(canonical)
        return rf"(?:DECIMAL|NUMERIC)\s*\(\s*{precision}\s*,\s*{scale}\s*\)"
    if canonical in {"DOUBLE", "DOUBLE PRECISION"}:
        return r"(?:DOUBLE(?:\s+PRECISION)?)"
    if canonical in {"FLOAT", "REAL"}:
        return r"(?:FLOAT|REAL)"
    if canonical in {"INTEGER", "BIGINT", "HUGEINT"}:
        return re.escape(canonical)
    raise UnsafeNumericMigration(f"Unknown exported declaration: {actual}")


def rewrite_export_schema(schema_sql: str, changes: list[Column]) -> str:
    """Rewrite only verified CREATE TABLE column declarations in DuckDB export.

    This is deliberately strict: missing, ambiguous or differently typed
    declarations abort the rebuild, leaving the original untouched. No views,
    constraints, indexes, sequences or data are rewritten.
    """
    needed = {(c.table.upper(), c.name.upper()): c for c in changes}
    if len(needed) != len(changes):
        raise UnsafeNumericMigration("Duplicate table.column conversion in audit")
    matched: set[tuple[str, str]] = set()
    chunks: list[str] = []
    position = 0
    for table in _CREATE_TABLE.finditer(schema_sql):
        name = (table.group("quoted") or table.group("bare")).upper()
        end = schema_sql.find(";", table.end())
        if end == -1:
            raise UnsafeNumericMigration(f"Missing semicolon in exported {name} CREATE TABLE")
        chunks.append(schema_sql[position : table.end()])
        body = schema_sql[table.end() : end]
        for (target_table, target_column), item in needed.items():
            if target_table != name:
                continue
            if (target_table, target_column) in matched:
                raise UnsafeNumericMigration(f"Multiple CREATE TABLE entries: {name}")
            assert item.target is not None
            column = re.escape(target_column)
            pattern = re.compile(
                rf'(?P<prefix>(?:^|,)\s*(?:"{column}"|{column})\s+)'
                rf"(?P<type>{_type_regex(item.actual)})(?![A-Za-z0-9_])",
                flags=re.IGNORECASE | re.DOTALL,
            )
            body, replacements = pattern.subn(
                lambda m, dtype=item.target: m.group("prefix") + dtype, body
            )
            if replacements != 1:
                raise UnsafeNumericMigration(
                    f"Expected one {name}.{target_column} ({item.actual}) declaration "
                    f"in exported schema, found {replacements}. No database was changed."
                )
            matched.add((target_table, target_column))
        chunks.append(body)
        position = end
    chunks.append(schema_sql[position:])
    if matched != set(needed):
        raise UnsafeNumericMigration(
            f"Missing exported CREATE TABLE declarations: {sorted(set(needed) - matched)}"
        )
    return "".join(chunks)


def _sql_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def _table_rows(connection: Any) -> dict[tuple[str, str], int]:
    tables = connection.execute(
        "SELECT table_schema,table_name FROM information_schema.tables "
        "WHERE table_type='BASE TABLE' AND table_schema NOT IN "
        "('information_schema','pg_catalog') ORDER BY 1,2"
    ).fetchall()
    return {
        (str(schema), str(name)): _count(
            connection, f"SELECT COUNT(*) FROM {_ident(schema)}.{_ident(name)}"
        )
        for schema, name in tables
    }


def _content_fingerprint(connection: Any, columns: list[Column]) -> dict[str, str]:
    """Commutative per-table checksum over all rows and all original columns.

    Compare source rows after explicit *audited* casts with imported rows.
    Because row order is not guaranteed by export/import, aggregate hashes;
    also check counts independently. This is not a cryptographic proof, but
    detects accidental omissions and ordinary conversion changes.
    """
    grouped: dict[str, list[Column]] = {}
    for item in columns:
        grouped.setdefault(item.table, []).append(item)
    results: dict[str, str] = {}
    for table, items in grouped.items():
        expressions = [
            _cast_expression(item) if item.needs_conversion else _ident(item.name) for item in items
        ]
        if not expressions:
            continue
        sql = (
            f"SELECT CAST(SUM(CAST(HASH(ROW({', '.join(expressions)})) "
            f"AS DECIMAL(38,0))) AS VARCHAR) FROM {_ident(table)}"
        )
        item = connection.execute(sql).fetchone()[0]
        results[table] = str(item) if item is not None else "EMPTY"
    return results


def _catalog_signature(connection: Any) -> dict[str, list[tuple[str, ...]]]:
    """Check that rebuilding has not discarded constraints, indexes or sequences."""
    queries = {
        "relations": "SELECT table_schema,table_name,table_type "
        "FROM information_schema.tables WHERE table_schema NOT IN "
        "('information_schema','pg_catalog') ORDER BY 1,2,3",
        "constraints": "SELECT schema_name,table_name,constraint_type "
        "FROM duckdb_constraints() ORDER BY 1,2,3",
        "indexes": "SELECT schema_name,table_name,index_name FROM duckdb_indexes() ORDER BY 1,2,3",
        "views": "SELECT schema_name,view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1,2",
        "sequences": "SELECT schema_name,sequence_name FROM duckdb_sequences() ORDER BY 1,2",
    }
    return {
        key: [tuple(str(v) for v in row) for row in connection.execute(sql).fetchall()]
        for key, sql in queries.items()
    }


_CREATE_SEQUENCE = re.compile(
    r"\bCREATE\s+SEQUENCE\s+"
    r"(?:(?:\"[^\"]+\"|[A-Za-z_][A-Za-z_0-9]*)\s*\.\s*)?"
    r"(?:\"(?P<quoted>[^\"]+)\"|(?P<bare>[A-Za-z_][A-Za-z_0-9]*))",
    re.IGNORECASE,
)


def _sequence_starts(connection: Any) -> dict[str, int]:
    """Prevent duplicate generated IDs after export/import (which redefines sequences).

    This rule is restricted to Ikaros's monotonic *_ID_SEQ sequences. Unknown
    used sequences cannot be repaired automatically and block the rebuild.
    """
    sequences = connection.execute(
        "SELECT schema_name, sequence_name, start_value, increment_by, "
        "last_value, cycle FROM duckdb_sequences() ORDER BY schema_name,sequence_name"
    ).fetchall()
    known_tables = {
        (str(schema).upper(), str(name).upper())
        for schema, name in connection.execute(
            "SELECT table_schema,table_name FROM information_schema.tables "
            "WHERE table_type='BASE TABLE'"
        ).fetchall()
    }
    starts: dict[str, int] = {}
    for schema, name, first, increment, last, cycle in sequences:
        name = str(name).upper()
        schema = str(schema).upper()
        # Unused sequences have not advanced; retain their exported START.
        if last is None:
            continue
        if not name.endswith("_ID_SEQ") or increment != 1 or cycle:
            raise UnsafeNumericMigration(
                f"Used sequence {schema}.{name} has no safe ID restart policy; "
                "the original database has not been changed"
            )
        table = name[: -len("_ID_SEQ")]
        if (schema, table) not in known_tables:
            raise UnsafeNumericMigration(f"Sequence {name} has no registered source table {table}")
        identifiers = {
            str(c).upper()
            for (c,) in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE UPPER(table_schema)=? AND UPPER(table_name)=?",
                (schema, table),
            ).fetchall()
        }
        possible = [c for c in ("CANDIDATE_ID", "PROVIDER_SYMBOL_ID", "ID") if c in identifiers]
        if len(possible) != 1:
            raise UnsafeNumericMigration(
                f"Could not identify unique ID field for sequence {name} on table {table}"
            )
        max_id = _count(
            connection,
            f"SELECT COALESCE(MAX({_ident(possible[0])}),0) FROM {_ident(schema)}.{_ident(table)}",
        )
        target = max(int(first), max_id + 1)
        starts[name] = target
    return starts


def rewrite_export_sequences(sql: str, starts: dict[str, int]) -> str:
    """Raise reused ID sequences' START values in the exported *new* schema."""
    encountered: set[str] = set()
    chunks: list[str] = []
    position = 0
    for match in _CREATE_SEQUENCE.finditer(sql):
        name = (match.group("quoted") or match.group("bare")).upper()
        if name not in starts:
            continue
        if name in encountered:
            raise UnsafeNumericMigration(f"Duplicate CREATE SEQUENCE for {name}")
        end = sql.find(";", match.end())
        if end == -1:
            raise UnsafeNumericMigration(f"Missing semicolon in CREATE SEQUENCE {name}")
        opts = sql[match.end() : end]
        # On DuckDB 1.x/2.x syntax: START [WITH] <value>.
        start_re = re.compile(r"\bSTART\s+(?:WITH\s+)?-?\d+\b", re.I)
        opts, count = start_re.subn(f"START WITH {starts[name]}", opts)
        if count == 0:
            opts = f" START WITH {starts[name]}" + opts
        elif count != 1:
            raise UnsafeNumericMigration(f"Ambiguous sequence START for {name}")
        chunks.append(sql[position : match.end()] + opts)
        position = end
        encountered.add(name)
    chunks.append(sql[position:])
    if encountered != set(starts):
        raise UnsafeNumericMigration(
            f"Missing sequence CREATE statements: {sorted(set(starts) - encountered)}"
        )
    return "".join(chunks)


def _check_sequences(connection: Any, starts: dict[str, int]) -> None:
    """Verify new sequence starts are strictly beyond all existing keys."""
    rows = connection.execute("SELECT sequence_name,start_value FROM duckdb_sequences()").fetchall()
    by_name = {str(name).upper(): int(start) for name, start in rows}
    for name, target in starts.items():
        if by_name.get(name) != target:
            raise UnsafeNumericMigration(
                f"Sequence {name} restart mismatch: wanted {target}, got {by_name.get(name)}"
            )


def rebuild(database: Path, *, output: Path, report: Path | None = None) -> int:
    """Export -> rewrite declared column types -> import into NEW db -> verify.

    Source opened strictly read-only; outputs and export directory must not
    exist. Source is never renamed, replaced, dropped or modified.
    """
    import duckdb
    from database.duckdb_file_lock import duckdb_access_lock

    database = database.expanduser().resolve()
    output = output.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    if output == database or output.exists():
        raise UnsafeNumericMigration(
            "Rebuild output must be a NEW, non-existing database path, distinct from source"
        )
    if output.suffix.lower() != ".duckdb":
        raise ValueError("--output must be a .duckdb file")
    export_dir = output.with_name(output.stem + "_parquet_export")
    if export_dir.exists():
        raise FileExistsError(f"Export directory exists: {export_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with duckdb_access_lock(database_path=database):
        with closing(duckdb.connect(str(database), read_only=True)) as source:
            columns, scans, unknown = preflight(source)
            payload = audit_payload(columns, scans, unknown)
            payload["database"] = str(database)
            _print_summary(payload)
            if report is not None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            if not payload["safe_to_convert"]:
                raise UnsafeNumericMigration("Preflight failed: nothing exported or replaced")
            changes = [c for c in columns if c.needs_conversion]
            if not changes:
                print("Already compliant. No rebuilt database needed.")
                return 0
            before_rows = _table_rows(source)
            before_schema = _catalog_signature(source)
            before_fingerprint = _content_fingerprint(source, columns)
            sequence_starts = _sequence_starts(source)
            # Parquet preserves the binary numeric values without the
            # intermediate CSV string parsing/roundtrips.
            print(f"EXPORT to: {export_dir}")
            source.execute(f"EXPORT DATABASE {_sql_literal(export_dir)} (FORMAT PARQUET)")
            schema_path = export_dir / "schema.sql"
            if not schema_path.is_file() or not (export_dir / "load.sql").is_file():
                raise UnsafeNumericMigration("Incomplete DuckDB export, source unchanged")
            original = schema_path.read_text(encoding="utf-8")
            rewritten = rewrite_export_schema(original, changes)
            rewritten = rewrite_export_sequences(rewritten, sequence_starts)
            if rewritten == original:
                raise UnsafeNumericMigration("No schema modifications made; refusing import")
            schema_path.write_text(rewritten, encoding="utf-8")
            print(f"IMPORT to NEW database: {output}")
            try:
                with closing(duckdb.connect(str(output))) as candidate:
                    candidate.execute(f"IMPORT DATABASE {_sql_literal(export_dir)}")
                    candidate.execute("CHECKPOINT")
                with closing(duckdb.connect(str(output), read_only=True)) as candidate:
                    post_columns, post_scans, post_unknown = preflight(candidate)
                    post = audit_payload(post_columns, post_scans, post_unknown)
                    if (
                        not post["safe_to_convert"]
                        or post["planned_conversions"]
                        or post["floating_columns"]
                    ):
                        raise UnsafeNumericMigration(
                            "Imported database still has nonconforming numeric columns"
                        )
                    _check_sequences(candidate, sequence_starts)
                    after_rows = _table_rows(candidate)
                    after_fingerprint = _content_fingerprint(candidate, post_columns)
                    if before_fingerprint != after_fingerprint:
                        raise UnsafeNumericMigration(
                            "Row-content checksums differ after import. Source unchanged."
                        )
                    if before_rows != after_rows:
                        changed = sorted(
                            k
                            for k in before_rows.keys() | after_rows.keys()
                            if before_rows.get(k) != after_rows.get(k)
                        )
                        raise UnsafeNumericMigration(f"Table row counts changed: {changed}")
                    after_schema = _catalog_signature(candidate)
                    for key in before_schema:
                        if before_schema[key] != after_schema[key]:
                            raise UnsafeNumericMigration(
                                f"Catalog objects changed during rebuild: {key}. "
                                "Source is untouched; inspect the export before retrying."
                            )
                    for row in scans:
                        table, name = _ident(str(row["table"])), _ident(str(row["column"]))
                        original_count = row["non_null"]
                        actual_count = _count(candidate, f"SELECT COUNT({name}) FROM {table}")
                        if actual_count != original_count:
                            raise UnsafeNumericMigration(
                                f"NULL counts changed in {row['table']}.{row['column']}"
                            )
            except BaseException:
                # Never leave a partial candidate with a seemingly valid name.
                for suffix in ("", ".wal"):
                    temporary = Path(str(output) + suffix)
                    if temporary.exists():
                        temporary.unlink()
                raise
    shutil.rmtree(export_dir)
    print(f"REBUILD VERIFIED: {output}")
    print("ORIGINAL UNCHANGED. Audit the new DB with --database NEW.duckdb first.")
    print("Do not swap file names while Ikaros or Daedalus is running.")
    return 0
