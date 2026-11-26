# Ikaros installation

Follow the [root README](README.md) for the local one-click installation. It is the authoritative newcomer workflow.

For Windows, `setup-ikaros.cmd` creates the project-local `Ikaros/` environment. To install or migrate the environment only, run `setup-ikaros.cmd -EnvironmentOnly -Developer` (omit `-Developer` for a fresh local-only installation). Existing connector extras are retained. The old `.venv` is not deleted. See the README for PyCharm interpreter selection.

For Linux/macOS, install uv from https://docs.astral.sh/uv/getting-started/installation/ and run from the project root:

```sh
export UV_PROJECT_ENVIRONMENT=Ikaros
# First installation only:
uv venv --python 3.12 --prompt Ikaros Ikaros
# First installation and subsequent updates:
uv sync --locked --python 3.12 --inexact
./Ikaros/bin/python -m run.prepare_daedalus
```

For development, add `--all-extras` to the sync command. Use the existing lockfile; maintainers should run `uv lock` and commit it when dependencies change. The environment name setting is local to your shell. Make uses the named interpreter directly.
