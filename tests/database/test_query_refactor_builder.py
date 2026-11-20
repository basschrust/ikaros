"""P1 query extraction keeps the public API and moves DB SQL out of callers."""

from __future__ import annotations

from tools.build_db_query_refactor import convert_module, build_package


def test_sql_function_is_moved_with_original_signature_and_constants():
    original = """from __future__ import annotations
from contextlib import closing
MERGE_SQL = "MERGE INTO PRICES t USING s ON 1=0 WHEN NOT MATCHED THEN INSERT VALUES (1)"

def persist(connection, batch: list[tuple], *, write: bool = True) -> int:
    if not write: return 0
    with closing(connection.cursor()) as cursor:
        cursor.execute(MERGE_SQL, batch)
        return cursor.rowcount
"""
    # Need an importable module so the delegated function can synchronize globals.
    replacements, issues = convert_module("run/sample.py", original)
    assert not issues
    assert "MERGE INTO PRICES" not in replacements["run/sample.py"]
    assert "cursor.execute" not in replacements["run/sample.py"]
    assert (
        "def persist(connection, batch: list[tuple], *, write: bool=True)"
        in replacements["run/sample.py"]
        or "def persist" in replacements["run/sample.py"]
    )
    assert "MERGE INTO PRICES" in replacements["database/isolated/run_sample.py"]
    for file, contents in replacements.items():
        compile(contents, file, "exec")


def test_main_query_block_is_extracted_without_moving_orchestrator():
    source = """from __future__ import annotations
QUERY = "SELECT value FROM prices"
def main():
    config = load_config()
    with get_repository(config) as repo:
        with repo.connection.cursor() as cur:
            cur.execute(QUERY)
            records = cur.fetchall()
        print(records)
"""
    converted, problems = convert_module("run/check.py", source)
    assert not problems
    assert "def main" in converted["run/check.py"]
    assert "print(records)" in converted["run/check.py"]
    assert ".cursor()" not in converted["run/check.py"]
    assert "records = _inline_main_" in converted["run/check.py"]
    assert "SELECT value FROM prices" in converted["database/isolated/run_check.py"]


def test_missing_checkout_files_are_reported_not_invented(tmp_path):
    _, issues = build_package(tmp_path, tmp_path / "result.zip")
    assert any("file missing" in item.problem for item in issues)
    assert (tmp_path / "result.zip").is_file()
