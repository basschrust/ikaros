# Ikaros 🪽 — Market Data Ingestion

**OpenBB / Yahoo Finance → Ikaros collectors → Kafka → Ikaros consumers → Snowflake (`DAEDALUS.MARKET_DATA`)**

Ikaros maintains daily GPW equity prices and FX rates in Snowflake for Daedalus. It also discovers GPW derivative **candidates**; discovery is **not** the same as verified contract registration or derivative-price ingestion.

## Quick start (Windows 10/11, PowerShell)

**Prerequisites (once):** Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) with Linux containers/WSL 2, [uv](https://docs.astral.sh/uv/), and Python 3.12 (uv can install Python). Docker Desktop must be running and your Windows account must have permission to use it. Ensure `config/database.yaml` selects the Snowflake driver and configure the `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and (for password authentication) `SNOWFLAKE_PASSWORD` environment variables outside Git. Alternative authentication must match `config/database.yaml`. **Never commit credentials.** The Snowflake role must have permission to read reference tables and write observations and control tables. The repository must include `run/ikaros_scheduler.py`, `run/kafka_contract_sink.py`, and `run/kafka_discovery_topic.py` from the scheduler extension.

### Two launches

1. **One-time installation:** Double-click **`setup-ikaros.cmd`** in the repository root. It installs the Python environment, starts the local Kafka broker, creates Kafka topics and checks connectivity. It does **not** run a full historical backfill or silently seed new securities.
2. **Start the engine:** Double-click **`start-ikaros.cmd`**. It starts the Kafka broker (if needed), FX and GPW Snowflake sinks, the derivatives discovery sink, and the ingestion scheduler in **separate Windows terminal windows**. Leave these windows open. Start only **one** scheduler instance. The launcher also starts a separate GPW catch-up window for the last 7 calendar days, with IIC-backed idempotent inserts, to help close recent gaps.

These are two local *launch actions*, not unattended provisioning of Docker, Snowflake credentials, provider permissions or the instrument universe. **The scheduled collectors require an existing seeded universe.** First-time data setup is described below.

### One-time data seeding (on a fresh Snowflake schema)

In PowerShell from the repository root:

```powershell
# Register GPW stocks from data/gpw_stocks.csv (ticker,name columns)
uv run --extra snowflake python -m run.seed_gpw_snowflake --write

# Register FX currency/pair reference data using the scripts already supplied with Ikaros.
# Review their supported flags first:
uv run --extra snowflake python -m run.seed_fx_reference --help
```

Confirm `SECURITIES` and `CURRENCY_PAIRS` are populated before enabling full collection. The scheduler defaults to `EURPLN=X` for FX unless `--fx-symbols` is configured. To collect your **entire supported direct FX universe**, edit the `FX_SYMBOLS` list in `tools/start-ikaros.ps1` to include the direct Yahoo-supported pairs seeded in Snowflake. **Do not include derived pairs unavailable from Yahoo**; calculate them using the cross-currency module.

### First historical load

On a new database, run this **once**, separately from the recurring 7-day catch-up:

```powershell
uv run --extra snowflake python -m run.backfill_gpw_snowflake --start 2020-01-01 --end 2026-10-27 --write --continue-on-error
```

Adjust `--end` to the most recently **completed** GPW trading date. IIC checkpoints successful exact symbol/date-window batches; historical backfill merges missing observations without overwriting preexisting rows. Avoid running the full backfill simultaneously with the streaming sink until you understand concurrency. For FX, use the existing `run.backfill_fx_snowflake` utility for supported registered pairs.

## Running manually / troubleshooting

If you prefer explicit PowerShell windows, start the following **in order**:

```powershell
# Broker and topics (once per broker initialization)
docker compose -f compose.kafka.yaml up -d
uv run --extra kafka python -m run.kafka_inbound_topics
uv run --extra kafka python -m run.kafka_discovery_topic

# Separate, continuously running consoles (each command blocks)
uv run --extra kafka --extra snowflake python -m run.kafka_sink --dataset FX
uv run --extra kafka --extra snowflake python -m run.kafka_sink --dataset GPW
uv run --extra kafka --extra snowflake python -m run.kafka_contract_sink
uv run --extra kafka --extra snowflake python -m run.ikaros_scheduler --write
```

The standard schedule is **FX every 5 minutes**, **GPW equities every 15 minutes**, **derivative candidate discovery every 6 hours**. A full GPW round can take *longer* than 15 minutes; actual freshness depends on the provider, the number of symbols and Snowflake availability. Polling daily EoD data does not create a real-time quote feed. Collectors may fetch and publish overlapping historical candles again; Snowflake sinks must remain idempotent.

### Check freshness and missing observations

```powershell
uv run --extra snowflake python -m run.gpw_freshness --csv output/gpw_freshness.csv
uv run --extra snowflake python -m run.iic_status --failed-only
```

The freshness monitor uses weekdays, **not** the official GPW holiday/suspension calendar; some instruments legitimately have no trade on a session. A stale row does not by itself prove the pipeline failed. Yesterday's candle may become available late from the provider. The one-week catch-up is useful for gaps but **does not overwrite an existing partial EoD price**; verify candle finality before using data for Daedalus.

For a specific missing completed day, use:

```powershell
uv run --extra snowflake python -m run.backfill_gpw_snowflake --start 2026-10-26 --end 2026-10-26 --write --continue-on-error
```

Replace the dates. For failed batches, inspect IIC before retrying; `--iic-force` forces a recheck but does not update existing price rows in the historical backfill.

### Verify in Snowflake

```sql
SELECT TO_DATE("DATE") AS SESSION_DATE,
       COUNT(DISTINCT "SYMBOL") AS EQUITIES,
       COUNT(*) AS OBSERVATIONS
FROM DAEDALUS.MARKET_DATA.PRICES
WHERE "DATE" >= DATEADD(day, -10, CURRENT_DATE())
GROUP BY 1 ORDER BY 1 DESC;
```

### Stop and restart

Close the four running process windows or use Ctrl+C in each. Stop Kafka (keep data):

```powershell
docker compose -f compose.kafka.yaml down
```

**Do not use `down -v`** unless you intentionally want to delete Kafka messages/offsets. The PowerShell launchers do not create a Windows service; to remain live, Docker Desktop and the collector/sink windows must keep running. They are suitable for development on a workstation, not a production high-availability deployment.

### Common problems

| Symptom | Check |
| --- | --- |
| `docker_engine` pipe not found | Start Docker Desktop; check Docker permissions and `docker info` |
| Kafka IPv6 `::1:9092` connection errors | Use `IKAROS_KAFKA_BROKERS=127.0.0.1:9092` and the IPv4 advertised listener in `compose.kafka.yaml` |
| `No module named pytest` | `uv sync --extra kafka --extra snowflake`, then `make test` (or `uv run python -m pytest -q tests`) |
| GPW data one or two sessions old | Run freshness monitor, then short catch-up; investigate provider gaps, holidays, suspensions |
| No FX symbols besides EURPLN | Set `FX_SYMBOLS` in `tools/start-ikaros.ps1` and seed the reference pairs |
| Derivative candidates present but no prices | Discovery only stages candidates; contract verification and a quotation source are still required |

For developer validation: `make test` and `make lint-check`.

**Current limitation:** This README and launchers simplify local operation; they do not guarantee 100% fresh daily data across every listed or suspended security, nor complete GPW derivative price coverage.
