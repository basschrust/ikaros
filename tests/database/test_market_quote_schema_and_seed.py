"""Offline Snowflake/DuckDB DDL and safe alias-seeding contract tests."""

from __future__ import annotations

from database.market_quote_schema import ensure_market_quote_schema
from database.market_quote_seed import seed_mappings


class Cursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, sql, params=()):
        self.db.statements.append((sql, tuple(params)))
        upper = sql.upper()
        if upper.startswith("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS"):
            table = upper.split("TABLE_NAME='")[1].split("'")[0] if "TABLE_NAME='" in upper else ""
            self.rows = [(x,) for x in self.db.columns.get(table, set())]
        elif upper.startswith("ALTER TABLE") and "ADD COLUMN" in upper:
            pieces = upper.split('"')
            table, column = pieces[1], pieces[3]
            if column in self.db.columns.get(table, set()):
                raise AssertionError(f"repeated ALTER: {table}.{column}")
            self.db.columns.setdefault(table, set()).add(column)
        elif upper.startswith('SELECT "PROVIDER_ID", "PROVIDER_CODE"'):
            self.rows = [(1, "YFINANCE"), (2, "BIZNESRADAR"), (3, "STOOQ")]
        elif upper.startswith('SELECT "CONTRACT_ID"') and 'FROM "DERIVATIVE_CONTRACTS"' in upper:
            self.rows = [("FW20Z2620",), ("OW20C2600",)]
        elif upper.startswith('SELECT "INDEX_SYMBOL"'):
            self.rows = [
                (k, pid, sym, "1900-01-01", None, True)
                for (k, pid), sym in self.db.aliases["INDEX"].items()
            ]
        elif upper.startswith('SELECT "DERIVATIVE_SYMBOL"'):
            raise AssertionError("wrong canonical column")
        elif upper.startswith('SELECT "CONTRACT_ID", "PROVIDER_ID"'):
            self.rows = [
                (k, pid, sym, "1900-01-01", None, True)
                for (k, pid), sym in self.db.aliases["DERIVATIVE"].items()
            ]
        elif upper.startswith('INSERT INTO "INDEX_PROVIDER_SYMBOLS"'):
            self.db.aliases["INDEX"][(params[0], params[1])] = params[2]
        elif upper.startswith('INSERT INTO "DERIVATIVE_PROVIDER_SYMBOLS"'):
            self.db.aliases["DERIVATIVE"][(params[0], params[1])] = params[2]
        elif upper in ("BEGIN", "COMMIT", "ROLLBACK"):
            return self
        elif upper.startswith("CREATE SEQUENCE") or upper.startswith("CREATE TABLE"):
            return self
        else:
            raise AssertionError(f"unexpected SQL {sql}")
        return self

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class DB:
    def __init__(self):
        self.statements = []
        self.columns = {
            "INDEX_PRICES": {"INDEX_SYMBOL", "DATE", "SOURCE"},
            "DERIVATIVE_PRICES": {"CONTRACT_ID", "DATE", "SOURCE"},
        }
        self.aliases = {"INDEX": {}, "DERIVATIVE": {}}

    def cursor(self):
        return Cursor(self)


def test_schema_creation_is_idempotent_snowflake():
    conn = DB()
    ensure_market_quote_schema(conn, backend="snowflake")
    first = len(conn.statements)
    assert sum("CREATE SEQUENCE IF NOT EXISTS" in sql for sql, _ in conn.statements) == 4
    assert any(
        '"INDEX_PRICE_CANDIDATES"' in sql and "NUMBER(38,0) PRIMARY KEY" in sql
        for sql, _ in conn.statements
    )
    assert any(
        '"DERIVATIVE_PRICE_CANDIDATES"' in sql and "NUMBER(24,8)" in sql
        for sql, _ in conn.statements
    )
    assert len([sql for sql, _ in conn.statements if sql.startswith("ALTER TABLE")]) == 8
    ensure_market_quote_schema(conn, backend="snowflake")
    assert not any(sql.startswith("ALTER TABLE") for sql, _ in conn.statements[first:])


def test_schema_declares_ints_on_duckdb():
    conn = DB()
    ensure_market_quote_schema(conn, backend="duckdb")
    sql = "\n".join(statement for statement, _ in conn.statements)
    assert '"CANDIDATE_ID" BIGINT PRIMARY KEY' in sql
    assert '"OPEN" DECIMAL(24,8)' in sql
    assert "CREATE SEQUENCE IF NOT EXISTS" in sql


def test_provider_seed_preserves_reviewed_aliases_and_replay():
    conn = DB()
    plan = seed_mappings(conn, backend="snowflake", write=False)
    assert plan == {"INDEX": 3, "DERIVATIVE": 2}
    assert not conn.aliases["INDEX"]
    assert seed_mappings(conn, backend="snowflake", write=True) == plan
    assert conn.aliases["INDEX"] == {
        ("WIG20", 1): "WIG20.WA",
        ("WIG20", 2): "WIG20",
        ("WIG20", 3): "wig20",
    }
    assert set(k for k, _ in conn.aliases["DERIVATIVE"]) == {"FW20Z2620", "OW20C2600"}
    assert seed_mappings(conn, backend="snowflake", write=True) == {"INDEX": 0, "DERIVATIVE": 0}
    assert not any(pid == 1 for _, pid in conn.aliases["DERIVATIVE"])
