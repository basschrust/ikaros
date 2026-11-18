# Ikaros — WIG20 index and GPW derivatives multi-source candidate pipeline

Target: `basschrust/ikaros`, branch `feature/clean_ge` (18 November 2026). This package is **additive** to the previously installed integer-CANDIDATE_ID migration. GitHub is read-only; no repository writes were made.

## Apply in this order

1. **Stop the older writers**: `run.refresh_index_prices`, `run.backfill_derivative_prices`, `run.refresh_fw20_prices`, scheduler and any `update_all` run. Otherwise they can bypass candidate selection.
2. Extract this ZIP **at the Ikaros repository root**, preserving all paths. The only overwritten production file is `run/update_all.py`; remaining Python modules are new. No core `database/schema.py` or historic equity ingestion changes are included.
3. The existing Snowflake `PROVIDERS` must contain `YFINANCE`, `STOOQ` and `BIZNESRADAR`; `DERIVATIVE_CONTRACTS` must contain any contracts you want loaded. A missing or inactive provider is never fabricated or enabled automatically.
4. One-time **schema + reviewed symbol seed**, as an Ikaros command:

   ```powershell
   uv run --extra snowflake python -m run.setup_market_sources --write
   ```

   This command creates/extends the tables idempotently in `DAEDALUS.MARKET_DATA`, then registers provider aliases (with a transaction). You can review schema changes in `sql/20261118_market_quote_sources.sql` instead and run it in Snowsight first. If you do, still run `run.setup_market_sources --write` to seed the mappings. Do **not** re-run legacy equity CANDIDATE_ID conversion SQL.
5. Format/test locally (Ruff and DuckDB were not installed in this isolated build environment):

   ```powershell
   make format
   make test
   make lint-check
   ```
6. Check both pipelines **without writing**:

   ```powershell
   uv run --extra snowflake python -m run.ingest_market_prices --dataset INDEX
   uv run --extra snowflake python -m run.ingest_market_prices --dataset DERIVATIVE
   ```

   The default interval is the **last five completed GPW sessions**. Explicit `--start YYYY-MM-DD --end YYYY-MM-DD` and `--symbols` are also supported.
7. After reviewing candidates and source warnings, write:

   ```powershell
   uv run --extra snowflake python -m run.ingest_market_prices --dataset INDEX --write
   uv run --extra snowflake python -m run.ingest_market_prices --dataset DERIVATIVE --write
   ```

   `--dataset DERIVATIVE` without a symbol list selects registered **futures and options** with expiries overlapping the requested period. A no-trade option is never padded with a synthetic candle.
8. The regular refresh is now routed through candidate selection:

   ```powershell
   uv run --extra snowflake python -m run.update_all --datasets WIG20 DERIVATIVES --write
   ```

   `GPW` remains on the existing multi-source equity worker. `FX`, other stock venues, `RATES` and the rest retain their previous workers. `FW20` (if explicitly requested instead of `DERIVATIVES`) is still the *legacy* single-source worker. `update_all` remains plan-only unless `--write` is supplied, and its existing bootstrap/staleness filters still apply.

## New database objects

| Table | Key | Meaning |
|---|---|---|
| `INDEX_PROVIDER_SYMBOLS` | integer `PROVIDER_SYMBOL_ID` | WIG20 -> provider ticker, with validity dates |
| `DERIVATIVE_PROVIDER_SYMBOLS` | integer `PROVIDER_SYMBOL_ID` | Exact `CONTRACT_ID` -> provider ticker |
| `INDEX_PRICE_CANDIDATES` | integer `CANDIDATE_ID` | Separate index OHLC observations, statuses, SHA-256 content fingerprints |
| `DERIVATIVE_PRICE_CANDIDATES` | integer `CANDIDATE_ID` | Separate contract OHLCV plus optional settlement and open interest |

Four corresponding `*_ID_SEQ` sequences allocate integer surrogate keys. `INDEX_PRICES` and `DERIVATIVE_PRICES` are created **only when missing**, and existing tables are extended with nullable integer `CANDIDATE_ID`, `PROVIDER_ID`, `SELECTED_AT`, `SELECTION_REASON`; no historical prices are dropped or overwritten by schema setup. Both Snowflake (`NUMBER(38,0)`) and DuckDB (`BIGINT`) DDL is present. The authoritative legacy tables retain their existing business keys and price types.

### Feed coverage

| Instrument | Concrete adapters | Seeded identifiers | Caveats |
|---|---|---|---|
| **WIG20 index** | BiznesRadar HTML, Stooq daily CSV, Yahoo daily/hourly | `WIG20`, `wig20`, `WIG20.WA` | Stooq CSV needs authorized API key (`IKAROS_STOOQ_APIKEY`); Yahoo sometimes has no daily index bars; HTML parsing must be checked live |
| **GPW futures** | BiznesRadar contract history | exact IDs from `DERIVATIVE_CONTRACTS` | No second independently verified historical OHLCV adapter yet |
| **GPW options** | BiznesRadar contract history | exact IDs from `DERIVATIVE_CONTRACTS` | Many dates may have no trades; never synthesize data; no independent second source yet |

All domains **support multiple mapped providers** and select one individually validated, complete observation. However, the derivatives side is initially a one-source pipeline until other *verified* contract histories and aliases are added. Legacy FW20 BiznesRadar scripts are **not** treated as an independent second provider.

## Selection and safety

* WIG20 index observations require consistent positive OHLC. The index has **no directly traded share volume**; source turnover is not injected as equity volume.
* Derivatives require consistent positive OHLC and an integer volume >= 0; optional open interest and settlement are never invented.
* Each source observation receives its own candidate row and validation status, with an integer primary key and separate SHA-256 `CONTENT_HASH` for replay deduplication.
* One valid provider suffices. For index source priority the explicit preference is BiznesRadar -> Stooq -> Yahoo; other observations are retained and disagreements are logged.
* **Never mix fields** from different providers. Existing valid authoritative rows that disagree with a newly selected observation remain unchanged and are reported as `PRESERVED_EXISTING_CONFLICT`. Matching historic rows gain provenance but keep their original OHLCV.
* The job must be run as **a single writer per dataset**. Snowflake standard tables do not enforce PK/UNIQUE constraints; we check the hash first, but cross-process simultaneous writes are unsupported.
* `--write` is required for the importer. The schema setup requires its own `--write`. Live Snowflake and upstream access were **not** verified in this session.

## Snowsight inspection

Run `sql/verify_market_quotes.sql` after a successful seed and ingestion. Check `INDEX_PRICES` and `DERIVATIVE_PRICES` for references to selected candidate rows and inspect rejection reasons. Check registered derivatives before assuming every option has trades.

## Local checks

**15 offline tests passed**, covering SQL-backed candidate saving, index and derivative promotion, one-source acceptance, replay, conflicts, rejected candidates, zero-volume option behavior, source fallback, exact mappings, schema generation for Snowflake/DuckDB, and update-all routing. The tests used a SQLite SQL-backed adapter and a test-only stand-in for the current repository's `ikaros_app.update.Job` to run outside the checkout. Real DuckDB/Snowflake connectivity, Ruff and the complete Ikaros suite have **not** been run here.

When you copy the package, run `make format`, `make test`, `make lint-check` and a Snowflake pilot before enabling a scheduler.
