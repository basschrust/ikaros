# Ikaros: persisted numeric precision (DuckDB and Snowflake)

## Why this change is necessary

`database/schema.py` historically declared populated `PRICES`/`FX_RATES`
columns as `DOUBLE`. `database/local_duckdb.py` only converted *empty* price
and FX tables, so the existing Daedalus-sourced `ikaros.duckdb` could contain
floating-point prices indefinitely. Other SQL creators also used `FLOAT`.

This release separates a strict **fixed-point persistent representation** from
floating-point quantities used *within* calculations. The policy is in
`database/numeric_contract.py`. It covers more than just OHLC columns.

## Numeric policy

| Domain | Column role | DuckDB | Snowflake |
|---|---|---|---|
| Securities, equities, indices, futures, options | Prices, strikes, settlement, dividends, split ratios | `DECIMAL(24,8)` | `NUMBER(24,8)` |
| FX published quotations | Open/high/low/close | `DECIMAL(24,10)` | `NUMBER(24,10)` |
| FX candidate-stage inputs | Open/high/low/close | `DECIMAL(24,8)` | `NUMBER(24,8)` |
| Interest rate/yield observations | Rate, bid, ask (fraction, not percent points) | `DECIMAL(24,10)` | `NUMBER(24,10)` |
| Trading volumes/open interest | Exact integer quantity | `DECIMAL(38,0)` | `NUMBER(38,0)` |
| Contract multipliers | Size per index point / contract | `DECIMAL(18,4)` | `NUMBER(18,4)` |
| Index constituent weights | Fraction | `DECIMAL(24,12)` | `NUMBER(24,12)` |
| Derived analytics (if persisted) | Returns, volatilities, ratio metrics | `DECIMAL(24,12)` | `NUMBER(24,12)` |
| Derived average volumes | Fractional rolling average | `DECIMAL(24,6)` | `NUMBER(24,6)` |

**Important:** The FX ingestion adapter and candidate store currently accept
only **eight decimal places**. The wider ten-place authoritative FX schema
prevents loss at the final storage layer but does not magically restore a
ninth/tenth digit discarded upstream. Widening the FX candidate pipeline is a
separate source-precision change and must be tested before advertising ten
source digits.

Integer IDs, primary keys, counters and timestamps remain their appropriate
native types; `BIGINT` is also an exact numeric representation. Decimal means
*exact storage after the source has been validated*, not infinite precision.

## Critical: existing `ikaros.duckdb` migration

**Stop ingestion and close DBeaver, Daedalus and any other DuckDB clients.**
The standalone `sql/audit_numeric_storage_duckdb.sql` is also provided for
read-only inspection in DBeaver. Run these commands from Ikaros's root folder in its activated Python
3.12 environment. The full database path is intentional: don't accidentally
convert a second Ikaros database under `output/market_data`.

1. Copy the replacement files from this package into Ikaros, preserving
   relative directories.
2. **Preview first** (read-only audit):

```powershell
python -m run.numeric_storage --database "C:/projects_AK/Daedalus/input/market_data/ikaros.duckdb" --report "output/numeric_storage_before.json"
```

3. Check every `BLOCKED` / `unsafe` line. The audit reports **all** physical
   floating-point columns in the database, including those outside the known
   price tables. An unknown column is *not automatically converted*.
4. If `SAFE TO MIGRATE` is printed, run:

```powershell
python -m run.numeric_storage --database "C:/projects_AK/Daedalus/input/market_data/ikaros.duckdb" --write
```

5. The tool checkpoints the database, closes the connection, and creates a
   complete backup named `ikaros.before_decimal_YYYYMMDD_HHMMSS_*.duckdb`
   **next to the original**. The original is converted inside a single
   DuckDB transaction. If a column conversion fails, the transaction is
   rolled back and the backup is preserved.
6. Re-run the audit, with no `--write`:

```powershell
python -m run.numeric_storage --database "C:/projects_AK/Daedalus/input/market_data/ikaros.duckdb"
```

It should report zero floating-point columns and zero planned conversions.
**Keep the backup** until all downstream Ikaros and Daedalus readback checks
pass. The migration never drops, truncates or recreates price tables.

The audit rejects non-finite/out-of-range values, fractional volumes and
legacy floating-point integer quantities beyond 2^53-1. It also checks
row counts and non-null counts after conversion. Rounding is explicit to
8 or 10 fractional digits as appropriate; missing original source precision
cannot be recovered from a historical DOUBLE. For a strict reconstruction,
reingest values from immutable original provider quotations instead of
assuming the rounded legacy FLOAT is identical to those quotations.

This implementation cannot be executed against the user's live database from
ChatGPT. A DuckDB integration test is supplied under `tests/`, but its
execution requires the Ikaros environment's DuckDB installation.

## New installations and operational safeguards

`database/schema.py` now declares **exact types** for newly created core
DuckDB/Snowflake reference and market tables. `database/local_duckdb.py`
no longer performs the old empty-table-only conversion. It refuses to open
a local repository with incompatible persisted numeric columns and tells the
operator to run the explicit audit/migration command. This is intentional:
normal ingestion must not silently round old prices during startup.

The regular candidate-first path already stores GPW equity candidates as
`DECIMAL(24,8)` and index/derivative candles as decimals; the independent
rates candidate path uses `DECIMAL(24,10)`. The older Snowflake rate-table
creator is updated in this package as well.

## Snowflake and less common tables: migration still required

**The DuckDB converter does not touch Snowflake.** Current Snowflake data
could still contain FLOAT columns because `CREATE TABLE IF NOT EXISTS` does
not change preexisting tables. Use `sql/audit_numeric_storage_snowflake.sql`
(read-only) to enumerate them. Migration requires a separate scheduled
Snowflake change with a **zero-copy CLONE backup**, value checks, staged
`NUMBER` columns, verification, then an intentional cutover. Snowflake DDL
has different transactional semantics from DuckDB DDL.

Code inspection also found old or optional paths that require migration or
separate follow-up before declaring every Snowflake workflow compliant:

- `run/ingest_gpw_derivatives.py` and `run/collect_us_option_chains.py`:
  the NEW Snowflake DDL is corrected here, but any preexisting FLOAT table
  requires separate migration; the CSV/market feed should still be reviewed
  for raw source precision;
- `database/kafka_sink_queries.py`: the legacy Kafka MERGE casts are corrected
  here, but this path should remain behind the candidate-only write guard;
- `database/index_queries.py`: legacy index/stooq staging casts to FLOAT;
- `run/derive_fx_snowflake.py`: temporary cross-rate stage declared FLOAT;
- `database/isolated/` historical SQL implementations: some FLOAT declarations;
- existing `GPW_STOCK_METRICS_DAILY`: historical Snowflake rows require a
  migration even though the newly created table DDL is fixed in this package.

**These residual Snowflake-specific writers are not silently certified by this
DuckDB package.** Stop or guard a legacy writer until its precision semantics
and existing table are verified; don't treat a successful DuckDB audit as
certifying a different Snowflake database.

## Read/export contract

DuckDB's Python `fetchall()` normally exposes `DECIMAL` as `decimal.Decimal`.
For transferring rates to Daedalus, prefer Decimal-preserving Arrow/Parquet
columns or canonical fixed-scale **strings** for JSON/CSV exchange. Some
Pandas interfaces may coerce decimal values to `float64`, losing precision
again. Cross-language consumers must preserve DECIMAL precision and scale.

DuckDB arithmetic (especially division) may use floating point internally.
That is an **internal numerical computation detail**, not a license to publish
its unrounded result to a floating-point column. Explicitly quantize outputs
at the persistent boundary.

## Regression commands

```powershell
python -m pytest -q tests/test_numeric_storage_policy.py tests/test_numeric_storage_duckdb_integration.py tests/test_us_option_numeric_precision.py tests/test_rates_us_extended.py
python -m ruff check .
python -m ruff format --check .
python -m pytest -q tests
```

The package contains **complete files** with their project-relative paths.
There are no `.patch` files and no scripts in the repository root.
