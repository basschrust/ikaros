# Ikaros 🪽

**Market-data ingestion for Daedalus** — GPW equities, FX, WIG20 index data and GPW derivatives, with local Kafka transport and Snowflake storage. Ikaros retrieves and validates observations; Daedalus simulates markets. The two projects do not share simulation logic.

## 1. Quick start (Windows / local)

**You can:** ingest GPW equities and FX into Kafka/Snowflake, monitor data freshness, backfill missing historical sessions, discover derivatives contracts, and refresh WIG20 index and FW20 futures data when their provider modules are installed.

Requirements: Python **3.12**, [uv](https://docs.astral.sh/uv/), Docker Desktop with Linux containers, and a Snowflake account. Launch commands from the **repository root** in PowerShell.

```powershell
uv sync --python 3.12 --extra kafka --extra snowflake
Copy-Item .env.example .env
notepad .env
uv run --no-sync python -m run.ikaros providers
uv run --no-sync python -m run.ikaros setup
uv run --no-sync python -m run.ikaros start
```

- Set Snowflake credentials in `.env` before `setup`/`start`; see Section 2. Never commit `.env`.
- `setup` starts the Kafka broker and creates topics, **not** market reference data.
- `start` runs scheduler and sinks in a single foreground supervisor; **Ctrl+C** stops the children but leaves broker data intact. Only run one instance.
- `status` checks GPW freshness and IIC failures; `stop-broker` stops Kafka without deleting volumes.
- Default FX polling is **EURPLN=X only**; use `start --fx-symbols EURPLN=X USDPLN=X` for more direct pairs. GPW polling covers registered GPW securities. `--gpw-limit 3` is useful in a pilot.

**One-time backfill:** commands default to a read-only dry run; add `--write` after checking dates/symbols.

```powershell
# Historical GPW daily candles
uv run --no-sync python -m run.ikaros backfill --dataset GPW --start 2020-01-01 --end 2020-12-31
# Two direct FX pairs; supplying --symbols is deliberate
uv run --no-sync python -m run.ikaros backfill --dataset FX --symbols EURPLN=X USDPLN=X --start 2010-01-01 --end 2010-12-31
# Existing local optional extensions, when installed:
uv run --no-sync python -m run.ikaros backfill --dataset FX-NBP --start 2002-01-02 --end 2019-12-31
uv run --no-sync python -m run.ikaros backfill --dataset WIG20 --csv data/wig20.csv --start 1994-01-01 --end 2026-10-29
uv run --no-sync python -m run.ikaros backfill --dataset FW20 --end 2026-10-29
```

`FX-NBP`, `WIG20` and `FW20` historical modules were developed locally and **may not yet be on GitHub master**. The CLI tells you if an extension is absent. Reference rates from NBP are *close-only*, not traded OHLC bars. Backfills do not seed references automatically. `--write` modifies Snowflake; avoid concurrent backfills over the same intervals.

**Useful diagnostics:**

```powershell
uv run --no-sync python -m run.ikaros status
make test
make lint-check
docker compose -f compose.kafka.yaml ps
```

## 2. Configure Snowflake

1. Copy `.env.example` to `.env` and fill in `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` and `SNOWFLAKE_PASSWORD`. The new operator launcher loads `.env` if `python-dotenv` is installed, and exported OS variables take precedence. The existing `config/config.py` should also load `.env` for commands launched directly, as in the earlier dotenv integration.
2. Keep non-secret settings in `config/database.yaml`:

```yaml
database:
  driver: snowflake
  snowflake:
    database: DAEDALUS
    schema: MARKET_DATA
    warehouse: DAEDALUS_WH
    authenticator: snowflake
```

3. Create the Snowflake database and warehouse with an appropriately privileged role, ensure the account can create/read/write tables in `DAEDALUS.MARKET_DATA`, and grant only the access required for ingestion. The connector can create some tables but does **not** fully initialize historical reference securities/currencies by itself.
4. For a new schema, seed registered currencies/FX pairs and GPW securities **once**, after verifying the reference input files:

```powershell
uv run --extra snowflake python -m run.seed_fx_reference --all
uv run --extra snowflake python -m run.seed_fx_reference --all --write
uv run --extra snowflake python -m run.seed_gpw_snowflake --help
```

Stock seeding requires a real `data/gpw_stocks.csv`. Review the seed CLI before `--write`; it must not replace a populated reference universe blindly.

**Data checks (Snowsight):**

```sql
SELECT "SYMBOL", MIN(TO_DATE("DATE")) FIRST_DATE,
       MAX(TO_DATE("DATE")) LAST_DATE, COUNT(*) ROWS
FROM DAEDALUS.MARKET_DATA.FX_RATES
GROUP BY "SYMBOL" ORDER BY "SYMBOL";
```

Use a local `.env` only for development. Prefer a secrets manager or identity-based authentication for deployments. Never copy credentials into the README, source files, Git history or logs. `.gitignore` should contain `.env` and `.env.*` with an exception for `.env.example`.

## 3. Architecture

```text
                   CLI: python -m run.ikaros
                              |
              Application / operator orchestration
              ikaros_app.operations + scheduler
                    /                    \
        Provider adapters          Legacy import jobs
       ikaros_app.providers         historical backfills
                |                        |
     DailyCollector -> validated Observation events
                |                       |
          Kafka raw topics          direct bulk MERGE
                |                       |
          Kafka consumers -----------------+--> DAEDALUS.MARKET_DATA
                                                        |
                                       freshness + analytics + Daedalus
```

**Interfaces and responsibilities**

| Layer | Responsible for | Not responsible for |
| --- | --- | --- |
| `ikaros_app.providers.PriceHistoryProvider` | Contract for historical daily observations | Kafka, Snowflake, scheduling |
| `OpenBBFXProvider`, `OpenBBEquityProvider` | Provider-specific retrieval | Persistence |
| `ikaros_app.collection.DailyCollector` | Date bounds, normalization, validation, `Observation` creation | Network transport |
| `run.kafka_collect` | Kafka publication (thin compatibility wrapper) | Parsing provider response |
| `ikaros_app.operations.ProcessRunner` | Process supervision, local `.env`, CLI commands | Market calculations |
| `run.ikaros_scheduler` | Recurring job cadence | Backfills |
| `run.kafka_sink`, `run.kafka_contract_sink` | Persist confirmed Kafka events | Fetching market data |
| `ingestion/` | Transformations, checks, IIC, specialized index/derivative logic | Process management |
| `database/` | DuckDB/Snowflake repositories | Provider-specific API calls |

**Data quality:** no synthetic missing closes; invalid quotes are skipped with warnings; dated instrument reference records are distinct from observations; direct/derived/reference FX rates should carry provenance. IIC tracks batch attempts; rerunning an insert-only historical backfill does not rewrite existing prices. `INDEX_CONSTITUENTS` is a dated snapshot and should not be treated as automatically updated.

**Migration strategy:** `run/*.py` are still used by tests and scheduled jobs. This version refactors the **daily GPW/FX collector and operator orchestration first**, rather than deleting 30 scripts at once. Specialized provider modules (NBP, Stooq, BiznesRadar, WIG20) should move to the same protocol only after regression tests cover their source-specific semantics. Keeping their existing importers running is intentional.

**Known limitations:** local single-node Kafka, no automatic full historical backfill, market calendar gaps require investigation, some data providers rate-limit or restrict downloads. Never infer missing settlement/open interest values for futures.

## Development

Run `make test` and `make lint-check` before merging. See `tests/test_app_providers.py` and `tests/test_app_operations.py` for the new layer's offline contract tests.
