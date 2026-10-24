# Ruff linting for Ikaros (Python 3.12)

Copy `Makefile` and `ruff.toml` to the Ikaros project root. If you have custom Make targets, merge the new targets into your Makefile instead of overwriting it.

Install Ruff into your existing uv project (once):

```powershell
uv add --dev ruff
```

This updates your existing `pyproject.toml` and `uv.lock`. It does not replace existing application dependencies.

Commands:

```powershell
make lint
make lint-fix
make format
make format-check
make test
make check
```

`make lint` reports issues without modifying files. `make lint-fix` applies supported autofixes; `make format` changes source formatting; `make check` verifies lint, formatting and unit tests. For an existing codebase, first runs may report findings. Review all automated changes.

Without GNU Make:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest -q tests
```

`--no-sync` in the Makefile assumes you already ran `uv add --dev ruff` or `uv sync`. Remove that flag if you prefer automatic environment synchronization.

Do not overwrite your local `pyproject.toml`: Ruff's settings live in standalone `ruff.toml`. Commit `pyproject.toml`, `uv.lock`, `ruff.toml`, and `Makefile` when ready.
