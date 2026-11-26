# Ikaros installation

Follow the [root README](README.md) for the local one-click installation. It is the authoritative newcomer workflow.

For command-line or Linux use, install uv from https://docs.astral.sh/uv/getting-started/installation/, then from the repository root:

```sh
uv sync --locked --python 3.12
uv run --no-sync python -m run.prepare_daedalus
```

Use the existing lockfile; maintainers should run `uv lock` and commit it when dependencies change. For optional Snowflake/Kafka see the README. No GNU Make is required.
