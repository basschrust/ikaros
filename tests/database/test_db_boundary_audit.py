"""Architecture detector tests; independent of the live Ikaros checkout."""

from __future__ import annotations

from tools.audit_db_boundary import scan


def test_report_detects_query_and_backend_import_outside_database(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "database").mkdir()
    (tmp_path / "run" / "cmd.py").write_text(
        "from snowflake.connector import SnowflakeConnection\n"
        "def persist(conn):\n"
        '    conn.cursor().execute("INSERT INTO PRICES VALUES (1)")\n'
    )
    (tmp_path / "database" / "ignored.py").write_text(
        'def writer(conn):\n    conn.execute("INSERT INTO PRICES VALUES (1)")\n'
    )
    findings = scan(tmp_path)
    assert any("Backend-specific import" in x[2] for x in findings)
    assert any("SQL literal" in x[2] for x in findings)
    assert any("DB method" in x[2] for x in findings)
    assert all("database/ignored.py" not in x[0] for x in findings)


def test_scripts_under_top_level_sql_are_reported(tmp_path):
    (tmp_path / "sql").mkdir()
    (tmp_path / "sql" / "sample.sql").write_text("SELECT 1")
    assert scan(tmp_path) == [("sql/sample.sql", 1, "SQL file outside database/")]
