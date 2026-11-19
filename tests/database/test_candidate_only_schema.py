"""Additive schema and fixed-point/identity contract checks."""

from datetime import date
from decimal import Decimal
from database.candidate_observations import Observation, _valid, ensure_schema


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.last = ""

    def execute(self, sql, params=()):
        self.conn.queries.append(sql)
        self.last = sql
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


class Connection:
    def __init__(self):
        self.queries = []

    def cursor(self):
        return Cursor(self)


def test_snowflake_schema_separates_fx_and_rate_int_keys():
    con = Connection()
    ensure_schema(con, backend="snowflake")
    sql = "\n".join(con.queries)
    for table in ("FX_RATE_CANDIDATES", "RATE_OBSERVATION_CANDIDATES"):
        assert f'CREATE TABLE IF NOT EXISTS "{table}"' in sql
        assert f'CREATE SEQUENCE IF NOT EXISTS "{table}_ID_SEQ"' in sql
    assert sql.count('"CANDIDATE_ID" NUMBER(38,0) PRIMARY KEY') == 2
    assert '"RATE" NUMBER(24,10)' in sql
    assert '"VOLUME" NUMBER(38,0)' in sql
    assert "DROP TABLE" not in sql and "OR REPLACE" not in sql
    assert 'ALTER TABLE "FX_RATES" ADD COLUMN "CANDIDATE_ID" NUMBER(38,0)' in sql
    assert 'ALTER TABLE "RATE_OBSERVATIONS" ADD COLUMN "CANDIDATE_ID" NUMBER(38,0)' in sql


def test_schema_idempotent_no_destructive_ddl():
    con = Connection()
    ensure_schema(con, backend="snowflake")
    ensure_schema(con, backend="snowflake")
    assert not any("DROP" in sql or "REPLACE" in sql for sql in con.queries)


def test_non_positive_rate_valid_and_non_positive_fx_invalid():
    dt = date(2026, 11, 18)
    assert (
        _valid(Observation("RATE", "UST", dt, "UST", quote_kind="RATE", rate=Decimal("-0.005")))[0]
        == "VALID"
    )
    assert (
        _valid(
            Observation(
                "FX",
                "EURPLN=X",
                dt,
                "YFINANCE",
                ref_id=1,
                open=Decimal("4.1"),
                high=Decimal("4.2"),
                low=Decimal("4"),
                close=Decimal("-4"),
            )
        )[0]
        == "REJECTED"
    )
