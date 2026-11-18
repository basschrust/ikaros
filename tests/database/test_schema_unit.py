"""Offline schema contract tests using a catalog-aware Snowflake simulation."""

from __future__ import annotations

import pytest

from database.schema import BASE_COLUMNS, NEW_COLUMNS, _type, prepare_tables


class SnowflakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql):
        self.connection.statements.append(sql)
        upper = sql.upper()
        if upper.startswith("CREATE TABLE IF NOT EXISTS"):
            table = upper.split('"')[1]
            self.connection.tables.setdefault(
                table, set(c.upper() for c in ((BASE_COLUMNS | NEW_COLUMNS)[table.lower()]))
            )
        elif upper.startswith('SELECT "COLUMN_NAME" FROM INFORMATION_SCHEMA.COLUMNS'):
            table = upper.split("TABLE_NAME = '")[1].split("'")[0]
            self.rows = [(c,) for c in self.connection.tables[table]]
        elif upper.startswith("ALTER TABLE") and " ADD COLUMN " in upper:
            table, column = upper.split('"')[1], upper.split('"')[3]
            if column in self.connection.tables[table]:
                raise AssertionError(f"repeated ALTER for {table}.{column}")
            self.connection.tables[table].add(column)
        elif 'SELECT "ASSET_ID", COUNT(*)' in upper:
            self.rows = []
        elif "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS" in upper:
            self.rows = [(int(self.connection.primary_key),)]
        elif " ADD CONSTRAINT " in upper:
            self.connection.primary_key = True
        elif upper.startswith('UPDATE "PRICES"'):
            pass
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")
        return self

    def fetchall(self):
        return self.rows


class SnowflakeConnection:
    """Small simulated SQL catalog, including metadata and ALTER semantics."""

    def __init__(self):
        self.statements = []
        self.tables = {}
        self.primary_key = False

    def cursor(self):
        return SnowflakeCursor(self)


def test_full_schema_has_eleven_tables() -> None:
    assert len(BASE_COLUMNS) == 8
    assert len(NEW_COLUMNS) == 3
    assert set(NEW_COLUMNS) == {"providers", "provider_symbols", "prices_candidates"}


def test_snowflake_create_all_and_extensions() -> None:
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    ddl = "\n".join(conn.statements)
    for name in (*BASE_COLUMNS, *NEW_COLUMNS):
        assert f'CREATE TABLE IF NOT EXISTS "{name.upper()}"' in ddl
    # Fresh CREATE TABLE defines all columns already: no ALTER necessary.
    assert not any(" ADD COLUMN " in statement for statement in conn.statements)
    assert '"ASSET_ID" NUMBER(38,0)' in ddl
    assert '"CLOSE" NUMBER(24,8) NOT NULL' in ddl
    assert '"VOLUME" NUMBER(38,0)' in ddl
    assert 'FOREIGN KEY ("ASSET_ID") REFERENCES "SECURITIES"("ASSET_ID")' in ddl
    assert 'UPDATE "PRICES"' in ddl
    assert "DROP TABLE" not in ddl.upper()
    assert "CHECK (" not in ddl  # Snowflake standard tables omit CHECK constraints.


def test_snowflake_ddl_can_be_reissued() -> None:
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    first_count = len(conn.statements)
    prepare_tables(conn, backend="snowflake")
    second_run = conn.statements[first_count:]
    assert not any(" ADD COLUMN " in statement for statement in second_run)
    assert not any(" ADD CONSTRAINT " in statement for statement in second_run)
    assert all("DROP TABLE" not in statement.upper() for statement in second_run)


def test_bad_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="backend"):
        prepare_tables(SnowflakeConnection(), backend="mysql")


def test_unambiguous_canonical_asset_id_remains_primary() -> None:
    assert "asset_id" in BASE_COLUMNS["prices"]
    assert "provider_id" in BASE_COLUMNS["prices"]
    assert "provider_symbol_id" in NEW_COLUMNS["prices_candidates"]


def test_snowflake_schema_initializes_twice_without_repeated_alters():
    conn = SnowflakeConnection()
    prepare_tables(conn, backend="snowflake")
    statements_first = list(conn.statements)
    prepare_tables(conn, backend="snowflake")
    statements_second = conn.statements[len(statements_first) :]
    assert len(conn.tables) == len(BASE_COLUMNS) + len(NEW_COLUMNS) == 11
    assert not any(" ADD COLUMN " in s for s in statements_second)
    assert sum(" ADD CONSTRAINT " in s for s in conn.statements) == 1


def test_existing_database_adds_only_missing_columns():
    conn = SnowflakeConnection()
    conn.tables["SECURITIES"] = {"ASSET_ID", "SYMBOL"}
    conn.tables["PRICES"] = {"SYMBOL", "DATE", "CLOSE", "ASSET_ID", "PROVIDER_ID"}
    prepare_tables(conn, backend="snowflake")
    added = [s for s in conn.statements if " ADD COLUMN " in s]
    assert len(added) == 4  # IS_ACTIVE and 3 remaining PRICES metadata columns
    assert all("IF NOT EXISTS" not in s for s in added)
    assert any('"IS_ACTIVE"' in s for s in added)
    assert not any('"PROVIDER_ID"' in s for s in added)
    before = len(conn.statements)
    prepare_tables(conn, backend="snowflake")
    assert not any(" ADD COLUMN " in s for s in conn.statements[before:])


def test_snowflake_default_timestamp_is_preserved():
    assert _type("TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP", "snowflake") == (
        "TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
    )
    assert _type("DECIMAL(24,8)", "snowflake") == "NUMBER(24,8)"
    assert _type("BIGINT", "snowflake") == "NUMBER(38,0)"


def test_schema_rejects_unknown_backend():
    import pytest

    with pytest.raises(ValueError, match="Unknown backend"):
        prepare_tables(SnowflakeConnection(), backend="sqlite")
