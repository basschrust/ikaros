# Ikaros: DuckDB / Snowflake backends

Based on `feature/gpw_ge`, using the Daedalus pattern: `RepositoryInterface`, a
factory, a native DuckDB adapter, and a native Snowflake connector.

## Installation

Overlay the provided directories/files into the Ikaros repository root. Keep your
existing `database/duckdb_storage.py` intact.

Optional Snowflake dependency:

```bash
python -m pip install -r requirements-snowflake.txt
```

Set `database.driver` in `config/database.yaml` to either `duckdb` or `snowflake`.
For Snowflake configure `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
`SNOWFLAKE_PASSWORD` in the environment; never commit secrets.

## Hook existing run scripts into the factory

In `run/basic_ingest.py`, `run/ingest_fx_rates.py`, `run/fill_securities.py`,
`run/fill_currencies.py`, `run/fill_calendars.py`, replace:

```python
from database.duckdb_storage import Repository
```

with:

```python
from database.repositories.factory import repository_from_config
```

Then replace `repository = Repository()` with:

```python
repository = repository_from_config(load_config("database.yaml"))
```

These scripts already import `load_config` on `feature/gpw_ge`.
The domain-level API remains unchanged.

Run `make test` to run the existing local tests plus the new factory tests.

## Constraints of this first stage

- The Snowflake schema (default `DAEDALUS.MARKET_DATA`) is persistent and shared.
  Unlike Daedalus, it is **not** generated per simulation run.
- Only **one** storage backend is selected at a time; no dual-write replication.
- Append-only behavior matches the current DuckDB repository. Re-runs can create
  duplicates; idempotent MERGE/upserts and ingestion checkpoints are later work.
- Tables are created automatically (requires relevant Snowflake privileges).
- Query DataFrame column names are normalized to lowercase across backends.
- No live Snowflake integration tests were run; production use requires account
  access, permissions, and validation against the actual Snowflake environment.
