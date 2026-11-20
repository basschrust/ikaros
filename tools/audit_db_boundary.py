"""Report database operations outside the dedicated database/ package.

Run from Ikaros root:
    python tools/audit_db_boundary.py
    python tools/audit_db_boundary.py --strict

The default is a read-only report. --strict exits 1 when violations remain.
Parser-based findings require review (e.g. third-party '.execute' calls).
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

SQL_PATTERN = re.compile(
    r"\b(?:SELECT\b.{0,120}?\bFROM\b|INSERT\s+INTO|MERGE\s+INTO|"
    r"UPDATE\s+[\w\"`]+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE|ALTER\s+TABLE|DROP\s+TABLE|"
    r"TRUNCATE\s+TABLE|SHOW\s+TABLES|INFORMATION_SCHEMA)\b",
    re.IGNORECASE | re.DOTALL,
)
DB_METHODS = frozenset(
    {
        "cursor",
        "execute",
        "executemany",
        "fetchone",
        "fetchall",
        "fetch_pandas_all",
        "_query",
        "write_pandas",
        "fetchdf",
    }
)
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "tests",
        "database",
        ".pytest_cache",
    }
)


def scan(root: Path) -> list[tuple[str, int, str]]:
    """Find candidate violations, without modifying any file."""
    result: list[tuple[str, int, str]] = []
    for source in sorted(root.rglob("*.py")):
        relative = source.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if relative.parts[0] == "tools":
            continue  # architecture tooling itself is not application persistence
        module = ast.parse(source.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in DB_METHODS:
                    result.append((str(relative), node.lineno, f"DB method: {node.func.attr}"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if SQL_PATTERN.search(node.value):
                    result.append((str(relative), node.lineno, "SQL literal"))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module.startswith("snowflake.connector") or node.module == "duckdb")
            ):
                result.append((str(relative), node.lineno, "Backend-specific import"))
            elif isinstance(node, ast.Import):
                if any(
                    x.name.startswith("snowflake.connector") or x.name == "duckdb"
                    for x in node.names
                ):
                    result.append((str(relative), node.lineno, "Backend-specific import"))
    for source in sorted((root / "sql").rglob("*.sql")) if (root / "sql").is_dir() else []:
        result.append((source.relative_to(root).as_posix(), 1, "SQL file outside database/"))
    return sorted(set(result))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    findings = scan(args.root)
    for filename, line, issue in findings:
        print(f"{filename}:{line}: {issue}")
    print(f"Database boundary audit: {len(findings)} findings")
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
