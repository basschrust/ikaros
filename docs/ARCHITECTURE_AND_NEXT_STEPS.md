# Candidate-first architecture and enforcement backlog

All database SQL and transaction handling added by this package lives in
`database/`. Feed calls and conversion remain under `ingestion/`; CLI workers
are in `run/`.

## Existing model

The established three candidate pipelines are reused as-is for selection:
`PRICES_CANDIDATES`, `INDEX_PRICE_CANDIDATES`, and
`DERIVATIVE_PRICE_CANDIDATES`. Selection is now made by a separate reconciler
**from committed candidate records**, not from the producer's in-memory
provider payload. Each provider's highest integer candidate ID within a day
wins its own revision history; revisions do not count as independent providers.

For the remaining two daily data types, `FX_RATE_CANDIDATES` and
`RATE_OBSERVATION_CANDIDATES` are additive tables. Rate series definitions
are reference metadata, not published daily observations. They may be added
in the staging phase; all daily values go through the candidates.

The `DATE` and `CANDIDATE_ID` combination in the final price domain must
resolve to a row in the domain's own candidate table. IDs are not reused
across unrelated candidate domains.

## Work needed before declaring GLOBAL strict mode

1. Redirect or disable legacy standalone `--write` CLI programs and the
   Kafka direct-to-final sinks. Do not just change `update_all`; old entrypoints
   remain callable. Keep historic one-off repair functions but expose only
   candidate evidence writes in production.
2. Implement GPW/foreign-equity DuckDB SQL bindings under the database layer;
   test Snowflake and DuckDB on the same normalized input and state transitions.
3. Separate stage and promotion privileges in Snowflake and CI; grant final
   `INSERT/UPDATE/MERGE` exclusively to the promoter role. This is the only
   reliable hard boundary against accidental direct scripts.
4. Inventory remaining datasets not in the daily planner, particularly US
   option chain snapshots and manually supplied derivative CSVs; give each
   its own validated candidate domain rather than reusing a price table with
   incompatible semantics.
5. Add source-specific conflict reviews and report candidate coverage per
   date/session, distinguishing exchange closure, missing data, and no-trade
   contracts. Continue the existing gap-repair logic.

This package should be deployed first to a cloned Snowflake environment and
verified using `database/sql/verify_candidate_flow.sql`.
