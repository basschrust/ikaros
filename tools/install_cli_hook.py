"""Opt-in: enable friendly DB connection messages for *all* venv Python scripts.

Run from the repository root:
    uv run python -m tools.install_cli_hook

No package installation or changes to individual scripts are needed.
Uninstall using: uv run python -m tools.install_cli_hook --remove
"""

from __future__ import annotations

import argparse
import sys
import sysconfig
from pathlib import Path

FILENAME = "ikaros_cli_errors.pth"


def hook_path() -> Path:
    """Never install into the global interpreter or outside this virtualenv."""
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Activate an Ikaros virtual environment (use 'uv run').")
    site_packages = Path(sysconfig.get_path("purelib")).resolve()
    if not site_packages.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"Refusing to modify a site-packages outside the venv: {site_packages}")
    return site_packages / FILENAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Remove the optional venv-wide hook")
    args = parser.parse_args(argv)
    try:
        target = hook_path()
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.remove:
        target.unlink(missing_ok=True)
        print(f"Removed Ikaros venv-wide error hook: {target}")
        return 0
    project_root = Path(__file__).resolve().parents[1]
    if not (project_root / "ikaros_app" / "cli_errors.py").is_file():
        parser.error("The Ikaros CLI error formatter is missing from ikaros_app/")
    target.write_text(
        f"{project_root}\nimport ikaros_app.cli_errors; ikaros_app.cli_errors.install()\n",
        encoding="utf-8",
    )
    print(f"Installed Ikaros venv-wide error hook: {target}")
    print("Now all Python scripts run with this interpreter use friendly DB errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
