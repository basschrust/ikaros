# SQL isolation audit — feature/clean_ge (commit b752f3b4)

## Scope and method

Inspected all production Python modules under `run/`, `ingestion/` and
`ikaros_app/` in GitHub, searching for SQL statements, cursor execution,
transactions, DDL, data access and Information Schema references. Most SQL
inside `database/` was already correctly located and is not part of this list.

This inventory is a **remaining-work register**, not evidence that all queries
have been moved. Re-audit the branch after copying a batch: the branch can
change between checks. Tests remain untouched except for the new regression
module in this ZIP.

## Completed in this ZIP

- `ikaros_app/gap_repair.py` — session bounds and observed dates
- `run/ingest_market_prices.py` — list of eligible registered derivatives
- `ingestion/index_prices_refresh.py` — legacy Yahoo index MERGE
- `ingestion/wig20_stooq.py` — overlaps and insert-only legacy backfill
- `run/refresh_index_prices.py` — table existence check
- `ingestion/kafka_snowflake_sink.py` — FX/GPW Kafka MERGE and pair lookup
- `run/iic_status.py` — operational summaries and failures
- `run/review_gpw_contracts.py` — review candidate listing
- `run/check_snowflake_tables.py` — connection metadata and table enumeration
- `run/smoke_fx_snowflake.py` — stored FX dates lookup
- `run/ikaros_scheduler.py` — GPW/FX symbol registration lookup

## Remaining production modules with DB-specific code

| Priority | File | Move into database/ |
| --- | --- | --- |
| P0 | `ikaros_app/update.py` | Multi-dataset inventory, most-recent dates |
| P0 | `ingestion/iic.py` | IIC DDL, state machine DB operations |
| P0 | `run/backfill_equities.py` | Security lookup, persisted dates, staging and merge |
| P0 | `run/backfill_fx_snowflake.py` | FX pair lookup, history, staging and merge |
| P0 | `run/backfill_fx_nbp.py` | Active FX pairs, NBP staging/merge |
| P0 | `run/backfill_rates.py` | Rate series DDL, upsert, conflict checks |
| P0 | `run/derive_fx_snowflake.py` | Cross-rate lookup and stage/merge |
| P0 | `ikaros_app/derivative_prices.py` | Registered contract query and derivative history persist |
| P0 | `run/refresh_fw20_prices.py` | Registered contracts and legacy quote MERGE |
| P0 | `run/repair_equity_prices.py` | Candidate replacement lookup and update |
| P1 | `ikaros_app/derivative_approval.py` | Reference check and approved contract promotion |
| P1 | `ikaros_app/derivative_specs.py` | Contract validation and spec inserts |
| P1 | `run/ingest_gpw_derivatives.py` | Contract/price DDL, staging, quote MERGE |
| P1 | `run/ingest_gpw_derivatives_bulk.py` | Direct execution of derivative DDL |
| P1 | `run/ingest_index_prices.py` | Index table DDL/legacy MERGE |
| P1 | `run/ingest_index_weights.py` | Constituents, validity ranges, replace inserts |
| P1 | `run/kafka_contract_sink.py` | Candidate DDL, upsert, registration DDL |
| P1 | `run/collect_us_option_chains.py` | Option snapshot DDL, staged merge and stock listing |
| P1 | `run/seed_equities.py` | Security lookup, ID allocation, batch insert |
| P1 | `run/seed_fx_reference.py` | Currency/pair lookup and reference inserts |
| P1 | `run/seed_snowflake_fx.py` | Seed FX reference lookup, ID allocation and inserts |
| P2 | `run/gpw_freshness.py` | Freshness query |
| P2 | `run/ingest_market_prices.py` | Completed |
| P2 | `run/ingest_index_prices.py` | See P1 |
| P2 | `run/check_snowflake_tables.py` | Completed |
| P2 | `run/iic_status.py` | Completed |
| P2 | `run/review_gpw_contracts.py` | Completed |
| P2 | `run/smoke_fx_snowflake.py` | Completed |

## Additional portability considerations

- Some direct-worker scripts have multiple DB-specific responsibilities; moving
  only constant SQL strings is insufficient. Transactions, staging-table DDL,
  cursor operations and backend-specific datetime casts also belong under
  `database/`.
- Reconcile the multiple current historical writers for `PRICES`, `FX_RATES`,
  `INDEX_PRICES`, and `DERIVATIVE_PRICES` before enabling concurrent jobs.
- Some fresh candidate pipelines already live fully under `database/`; do not
  rename tables or replay migrations as part of this refactor.
- Foreign equities, FX and rates are not yet uniformly candidate-validated;
  changing that is a separate behavior/schema project and should not be hidden
  inside a query-location refactor.

## Acceptance criterion for completion of the entire project

Once every P0/P1/P2 module is refactored, add a strict repository-wide AST rule:
**no SQL statement literals, direct `cursor()` calls or SQL-bearing
`execute()` calls outside `database/`**, excluding the test tree and genuine
non-SQL uses of `.execute()` (for example compute engines).

The new test in this batch applies that rule to the 11 callers completed here;
its scope must be expanded as the remaining modules are migrated.
