# Reproducible Ikaros Python 3.12 setup

Place these files at the root of your Ikaros repository (`run/check_environment.py` belongs in `run/`). The Makefile replaces the earlier test-only Makefile. No application logic changes are needed.

## First-time setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. (The `make` command additionally requires Make, available via Git Bash/MSYS2 or WSL on Windows.)
2. In the Ikaros repository root, run:

   ```bash
   make setup-cloud
   make check-env
   make test
   ```

   For DuckDB only, run `make setup` instead. `uv` obtains a Python 3.12 interpreter if required, creates `.venv/`, generates `uv.lock`, and installs the dependencies. `make setup-cloud` installs optional Snowflake/pandas support; it does **not** require Snowflake credentials to install.

   Without Make (PowerShell or CMD):

   ```bash
   uv sync --python 3.12 --extra dev --extra snowflake
   uv run --no-sync python -m run.check_environment
   uv run --no-sync python -m pytest -q tests
   ```

## Lock the environment for everyone

`pyproject.toml` specifies compatible version ranges. **Run `uv lock` on a network-connected machine and commit the resulting `uv.lock`** alongside `pyproject.toml`. This package does not include an already-resolved lockfile. Until you commit one, installs can choose different transitive versions.

On a new machine after the lockfile has been committed:

```bash
make sync
make test
```

Or with uv directly:

```bash
uv sync --locked --python 3.12 --extra dev --extra snowflake
```

`uv.lock` may be cross-platform, but you should test Windows/Linux installs before assuming every native wheel is available. `uv.lock` and `.python-version` should be committed; `.venv/`, `.env`, and private credentials must not be committed.

## Version choices

- Python is restricted to **3.12.x**.
- OpenBB is held at **4.7.2**. Ikaros currently calls `obb.yfinance.equity.price.historical` and `obb.currency.price.historical`, and OpenBB 5 changed package/endpoint organization. Verify both provider calls before upgrading.
- Snowflake connector is an optional extra (`[pandas]`) pinned below 5.0 initially. The Snowflake 5.x connector is a major version; validate against the Ikaros and Daedalus code before upgrading.
- pandas is restricted to `<3` to match Snowflake's pandas integration requirements.

## Validate cloud configuration separately

The environment check proves imports only. It does not log into Snowflake or create tables. Set `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and `SNOWFLAKE_PASSWORD` (or configure another authenticator) outside the repository. Then run your Snowflake schema smoke test.

## Notes

- `uv sync` may download dependencies; first-time setup needs internet connectivity.
- If existing project scripts import a missing third-party module, add it under `[project].dependencies` or the appropriate optional extra, regenerate `uv.lock`, and run the tests.
