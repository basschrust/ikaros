# Ikaros 🪽 — Market-data ingestion for Daedalus

**Public interface: `run.ikaros`.** The older `run/*.py` files are implementation modules; operators normally don't call them.

```text
OpenBB / GPW listings → Collectors → Local Kafka → Snowflake sinks → DAEDALUS.MARKET_DATA
                                                            ↓
                                                    Analytics SQL task
                                                            ↓
                                                         Daedalus
```

## First-time setup (Windows, PowerShell)

Prerequisites: Python 3.12 via [uv](https://docs.astral.sh/uv/), Docker Desktop running with Linux containers and appropriate permissions, network access to Snowflake, and environment variables `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` for password authentication. `config/database.yaml` must select `snowflake`. Never commit secrets. The same Snowflake schema must already contain the instrument reference data (`SECURITIES`, `CURRENCY_PAIRS`). For a fresh database, see **Bootstrap reference data** below.

```powershell
uv sync --python 3.12 --extra kafka --extra snowflake
uv run --no-sync python -m run.ikaros setup
```

This initializes Kafka and topics; it **does not** run historical backfills or create reference securities. Docker Desktop must be started by the user; the script cannot bypass Docker permission requirements.

## Run the engine (one command)

```powershell
uv run --no-sync python -m run.ikaros start
```

Keep this terminal open. **Ctrl+C stops the scheduler and three consumers**; Kafka remains running and keeps its data. Run **only one** copy of the engine; do not simultaneously use `start-ikaros.cmd` or open duplicate sink windows. Logs from all children appear together. On unexpected child failure, the supervisor stops the remaining processes instead of allowing a partially healthy pipeline to run unnoticed.

Default schedules (implemented by the existing `run.ikaros_scheduler`):

| Job | Interval | Output |
| --- | --- | --- |
| FX collector | 5 minutes | `FX_RATES` via Kafka |
| GPW equity collector | 15 minutes | `PRICES` via Kafka |
| Derivative contract discovery | 6 hours | `DERIVATIVE_CANDIDATES` via Kafka |

The default FX pair is `EURPLN=X`; **all** registered GPW equities are included. Override FX pairs with `--fx-symbols EURPLN=X USDPLN=X EURUSD=X`, using only provider-supported direct quotes. For a 3-stock pilot append `--gpw-limit 3`.

The discovery stream stores **candidates**, not verified contracts or option/future price histories. The stock analytics Snowflake task runs separately, provided it has been created and resumed. Kafka polling alone does not guarantee a finalized daily candle, a working exchange calendar, or that every security trades every day.

## Status and catch-up

Read-only freshness and failure reports:

```powershell
uv run --no-sync python -m run.ikaros status
```

This prints the weekday-based freshness report and writes `output/gpw_freshness.csv`; it also displays failed IIC batches. GPW holidays, trading suspensions and newly listed securities can legitimately create gaps.

If completed sessions are missing, use the backfill command with explicit **inclusive** dates; run it outside concurrent full-scale ingestion where possible:

```powershell
uv run --no-sync python -m run.ikaros backfill --start 2026-10-26 --end 2026-10-27
```

Backfill is **insert-only** for missing GPW `(symbol,date)` observations. It cannot correct an already-stored preliminary candle. Streaming sinks may refresh observations; check finality before using values in Daedalus. IIC considers exact date windows separate batches.

To stop only the Kafka server after shutting down Ikaros:

```powershell
uv run --no-sync python -m run.ikaros stop-broker
```

Do **not** use `docker compose down -v` unless you intend to delete broker messages and consumer offsets.

## Bootstrap reference data (fresh Snowflake only)

Do not do this on every launch. Seed GPW securities with a real `data/gpw_stocks.csv` file containing `ticker,name`:

```powershell
uv run --no-sync python -m run.seed_gpw_snowflake --write
uv run --no-sync python -m run.seed_fx_reference --help
```

Review the FX seed CLI's options before writing currency reference data. Initial years of historical GPW data can be loaded using the `backfill` command with the required period. This is separate from the recurring Kafka collector.

## System boundaries

- `run/ikaros.py` — operator CLI and foreground process supervisor (this package).
- `run/ikaros_scheduler.py` — cadence and collector orchestration.
- `run/kafka_collect.py`, `run/kafka_discover_contracts.py` — provider polling and Kafka publication.
- `run/kafka_sink.py`, `run/kafka_contract_sink.py` — Kafka consumers and Snowflake persistence.
- `ingestion/` — transformations, validation, IIC and freshness calculations.
- `providers/` — provider-specific adapters.
- `database/` — DuckDB/Snowflake connectors.
- `sql/stock_analytics/` — optional analytical layer and separate Snowflake task.

Legacy `run/*` command-line scripts stay present for tests, migration, recovery and debugging. Don't delete them until regression tests cover the new entry point. Existing Windows setup/start launchers remain **legacy alternatives**; use the unified CLI instead, not both.

## Diagnostics

```powershell
make test
make lint-check
docker compose -f compose.kafka.yaml ps
```

For Kafka IPv6 errors, use `127.0.0.1:9092` in `IKAROS_KAFKA_BROKERS` and the Kafka advertised listener. If `make test` says pytest is missing, run `uv sync --extra kafka --extra snowflake` (pytest is in the development dependency group). Your Snowflake credentials must be present in the **process environment**; the CLI does not read or create a `.env` file.

**Deployment note:** This is a local single-node Kafka development workflow, not a fault-tolerant production service. A daily price may be older than the most recent session because it wasn't traded or because the provider hasn't published it yet.
