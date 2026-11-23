# Local DuckDB historical backfill — one command

Based on Ikaros `feature/clean_ni` commit `c8734e4` (2026-11-21).
Copy the files into the root of your Ikaros checkout, preserving directory paths.
No existing files need to be overwritten.

## One-line full historical update

With the local DuckDB already initialized and FX initially populated:

```powershell
uv run python -m run.backfill_local --write
```

This **defaults to 2025-01-01 through yesterday (Europe/Warsaw)** and processes
all *registered, candidate-mapped* datasets, 60 days and 30 instruments at a
time. It does NOT use the daily incremental planner and therefore fills dates
older than the three FX days already present. It runs the existing
`run.stage_candidates` and `run.promote_candidates` processes in sequence.

Use without `--write` for a read-only execution plan (database connection and
schema initialization may still occur):

```powershell
uv run python -m run.backfill_local
```

For a small preliminary validation:

```powershell
uv run python -m run.backfill_local --datasets FX --start 2025-01-01 --through 2025-01-31 --write
```

For a customized selection:

```powershell
uv run python -m run.backfill_local --start 2025-01-01 --through 2026-11-20 --datasets FX GPW US WIG20 RATES DERIVATIVES --window-days 45 --batch-size 20 --write
```

`--fail-fast` stops on the first unsuccessful stage/promotion. Without it,
failed jobs are logged, unaffected jobs continue, and the overall command
returns nonzero. **A failed stage is never promoted.**

## Before running

Confirm Ikaros project `.env` includes:

```dotenv
IKAROS_DB_DRIVER=duckdb
IKAROS_MARKET_DATA_DIR=C:/projects_AK/Daedalus/input/market_data
```

Run `uv run python -m run.setup_local_duckdb --write` if any new reference
instruments were added. Only local DuckDB is used. Provider network requests
(Yahoo, NBP, equity feeds and rates providers) still take place.

If no series are mapped for a dataset, the command logs `SKIP` and continues.
An expired derivative contract must exist in `DERIVATIVE_CONTRACTS` and
`DERIVATIVE_PROVIDER_SYMBOLS` to be discovered. An absence of registered
historical contracts does **not** imply complete historical derivative coverage.

## Retry and completion

Candidate storage is content-hash-idempotent. Rerunning the same command is
safe but **re-fetches** completed windows; there is no checkpoint cache. For a
narrower retry, use `--start`, `--through` and `--datasets` to reduce work.

`source_errors` reported by a stage command are warnings in the current
Ikaros worker and may still yield exit code zero if some observations were
retrieved. A zero exit code therefore means all stage/promotion commands
returned success, **not** that every registered instrument had a full history.
Review `source_errors`, `REJECTED` candidates and final table coverage before
declaring the backfill complete. Untraded, delisted, newly listed and expired
securities may lack observations legitimately.

To inspect coverage in DuckDB (for example from Python), query:

```sql
SELECT 'PRICES' AS DOMAIN, MIN(CAST("DATE" AS DATE)) AS FIRST_DATE,
       MAX(CAST("DATE" AS DATE)) AS LAST_DATE, COUNT(*) AS ROWS FROM "PRICES"
UNION ALL
SELECT 'FX_RATES', MIN(CAST("DATE" AS DATE)), MAX(CAST("DATE" AS DATE)), COUNT(*) FROM "FX_RATES"
UNION ALL
SELECT 'INDEX_PRICES', MIN(CAST("DATE" AS DATE)), MAX(CAST("DATE" AS DATE)), COUNT(*) FROM "INDEX_PRICES"
UNION ALL
SELECT 'DERIVATIVE_PRICES', MIN(CAST("DATE" AS DATE)), MAX(CAST("DATE" AS DATE)), COUNT(*) FROM "DERIVATIVE_PRICES"
UNION ALL
SELECT 'RATE_OBSERVATIONS', MIN(CAST("DATE" AS DATE)), MAX(CAST("DATE" AS DATE)), COUNT(*) FROM "RATE_OBSERVATIONS";
```

## Checks

```powershell
uv run --no-sync python -m pytest -q tests/test_backfill_local.py
make lint-check
make test
```

No Snowflake configuration or connection is needed for the backfill CLI.
