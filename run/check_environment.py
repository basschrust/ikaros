"""Offline dependency check: python -m run.check_environment"""

from importlib import import_module, metadata
import sys

REQUIRED = {
    "duckdb": "duckdb",
    "pandas": "pandas",
    "numpy": "numpy",
    "yaml": "PyYAML",
    "openbb": "openbb",
}
OPTIONAL = {"snowflake.connector": "snowflake-connector-python", "pytest": "pytest"}


def check(modules: dict[str, str], *, optional: bool = False) -> int:
    failed = 0
    for module, distribution in modules.items():
        try:
            import_module(module)
            print(f"OK      {distribution:<28} {metadata.version(distribution)}")
        except (ImportError, metadata.PackageNotFoundError) as exc:
            print(f"{'OPTIONAL' if optional else 'MISSING ':<8} {distribution:<28} {exc}")
            if not optional:
                failed += 1
    return failed


def main() -> int:
    print(f"Python {sys.version.split()[0]} / {sys.executable}")
    if sys.version_info[:2] != (3, 12):
        print("WARNING: Ikaros is configured for Python 3.12")
    failures = check(REQUIRED)
    check(OPTIONAL, optional=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
