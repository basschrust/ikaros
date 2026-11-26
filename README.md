# Ikaros 🪽

**Market data for [Daedalus](https://github.com/basschrust/pymarketsim)** — retrieve, validate and store stock prices, WIG20, FX, official interest rates and registered GPW futures/options.

Ikaros can run on **one laptop, with DuckDB only**. A Snowflake account, Docker and Kafka are **not needed** for the simple historical and daily workflows.

## Quick start: five minutes to a local market database

Requirements: **Python 3.12**, [uv](https://docs.astral.sh/uv/), internet access for market-data providers. Commands below are for **PowerShell**, run from the Ikaros repository directory.

```powershell
git clone https://github.com/basschrust/ikaros.git
cd ikaros
uv sync --python 3.12
Copy-Item .env.example .env
uv run python -m run.setup_local_duckdb --write
```

The default `.env.example` selects DuckDB and puts `ikaros.duckdb` under `./output/market_data`. To use Ikaros directly as Daedalus's market-data source, edit `.env` and set:

```dotenv
IKAROS_DB_DRIVER=duckdb
IKAROS_MARKET_DATA_DIR=C:/path/to/Daedalus/input/market_data
```

Use your actual Daedalus directory (forward slashes also work on Windows). The database file will be `<IKAROS_MARKET_DATA_DIR>/ikaros.duckdb`. Never commit `.env` or credentials.

### Fetch market data

For a small first import, choose a recent start date (example):

```powershell
uv run python -m run.update_all --bootstrap-start 2026-11-16 --write
```

For a historical import **from 1 January 2025**, including already registered instruments with newer data:

```powershell
uv run python -m run.backfill_local --write
```

The backfill may take time and provider calls can fail; it uses 60-day windows and separate candidate staging and promotion. Registered equity universes require local, reviewed CSVs (`data/gpw_stocks.csv`, `data/us_sp500.csv`, `data/europe_pse.csv`, etc.). If none are supplied, those venues are reported as skipped rather than populated with fabricated tickers.

For ordinary daily incremental updates thereafter:

```powershell
uv run python -m run.update_all --write
```

This updates through **yesterday in Warsaw** by default, detects recent gaps for GPW/WIG20 and preserves existing accepted observations. To preview the plan, omit `--write`. To update just one domain, use `--datasets FX`, `--datasets GPW`, `--datasets WIG20`, `--datasets RATES` or `--datasets DERIVATIVES`.

## GPW futures and options: one CSV, one setup command

Unlike stocks, a derivative is a **specific contract with its own expiry**, not just an underlying symbol. Ikaros deliberately does not guess contract IDs or expiry dates. If `DERIVATIVE_CONTRACTS` is empty, the daily updater cannot fetch derivative prices.

**1. Copy the reviewed example manifest:**

```powershell
Copy-Item data/derivative_contracts.example.csv data/derivative_contracts.csv
```

The example contains two **verified November 2026** GPW symbols, both expiring on 18 December 2026:

```csv
contract_id,instrument_type,underlying_symbol,expiry_date
FW20Z2620,FUTURE,WIG20,2026-12-18
OW20L264250,OPTION,WIG20,2026-12-18
```

These are real examples, **not a live or automatically updated contract list**. Before using the file on another date, review and update the contract IDs and expiries using the [GPW exchange](https://www.gpw.pl/), [BiznesRadar futures](https://www.biznesradar.pl/gielda/pochodne_kontrakty,1,2) and [BiznesRadar options](https://www.biznesradar.pl/gielda/pochodne_opcje) listings. An option is a distinct row **for each listed strike, type and expiry**. For other underlyings, use their exact symbol as shown by the source. Do not invent contracts from a naming pattern.

**2. Register the contracts and their provider aliases:**

```powershell
uv run python -m run.setup_local_duckdb --write
```

`run.setup_local_duckdb` automatically finds `data/derivative_contracts.csv` when present. It creates the missing rows in `DERIVATIVE_CONTRACTS` and the corresponding `DERIVATIVE_PROVIDER_SYMBOLS` mappings for the existing BiznesRadar adapter. The command is **idempotent**: it does not overwrite historical prices or previously registered contract metadata. It refuses conflicting metadata. To use a different CSV filename, pass `--contracts-csv path/to/contracts.csv`.

**3. Import derivative prices:**

```powershell
uv run python -m run.update_all --datasets DERIVATIVES --write
```

The first daily import for a contract without existing prices checks a short recent window. For the complete history of **registered** contracts, including expired ones:

```powershell
uv run python -m run.backfill_local --datasets DERIVATIVES --write
```

This preserves candidate-first ingestion: provider data → `DERIVATIVE_PRICE_CANDIDATES` → independent validation/promotion → `DERIVATIVE_PRICES`. It never invents settlement or open interest values. An option may have no actual trades during a window; an empty result need not be a software error. If BiznesRadar changes its pages, source errors will be reported rather than silently replaced with synthetic prices.

**Every time new series are listed**, append reviewed rows to `data/derivative_contracts.csv` and rerun the **same setup command**. The daily updater will then include those contracts. Ikaros does not automatically discover newly listed contracts in this simple local workflow.

### What does the CSV mean?

| Column | Meaning |
| --- | --- |
| `contract_id` | Exact exchange/provider-listed symbol, e.g. `FW20Z2620` |
| `instrument_type` | `FUTURE` or `OPTION` |
| `underlying_symbol` | Underlying index or stock, e.g. `WIG20` |
| `expiry_date` | Verified expiry, ISO format `YYYY-MM-DD` |

### Check what was stored

Connect to the file from `.env` using DuckDB, DBeaver or a compatible SQL client and run:

```sql
SELECT * FROM DERIVATIVE_CONTRACTS ORDER BY EXPIRY_DATE, CONTRACT_ID;
SELECT * FROM DERIVATIVE_PROVIDER_SYMBOLS ORDER BY CONTRACT_ID;
SELECT CONTRACT_ID, MIN(DATE) AS FIRST_DATE, MAX(DATE) AS LAST_DATE,
       COUNT(*) AS CANDLES
FROM DERIVATIVE_PRICES GROUP BY CONTRACT_ID ORDER BY CONTRACT_ID;
```

If `DERIVATIVE_CONTRACTS` is still empty, check the path/name of `data/derivative_contracts.csv` and repeat setup. If mappings exist but prices are empty, inspect the `run.stage_candidates --dataset DERIVATIVES` source warnings and verify that BiznesRadar actually publishes historical rows for the selected contract.

## Other instruments and data quality

- **GPW and foreign stocks:** Ikaros only loads instruments registered from reviewed local equity CSVs, not all tickers available online. Register them with `run.setup_local_duckdb --write`; Yahoo and (for GPW) the configured additional sources supply observations.
- **WIG20:** registered during local setup; provider quotes pass validation before promotion.
- **FX:** the reference pairs are in `config/currencies.yaml`; Yahoo market OHLC and NBP reference close-only observations are different quote types. Missing weekends are normal for traded exchange sessions.
- **Rates:** official interest-rate series are not stock-market candles. Some providers publish observations with weekend dates; Ikaros keeps the source's actual dates rather than forcing a weekday calendar.
- **Source failures:** missing/invalid observations remain visible. Historical inserts and repeated updates do not overwrite good authoritative values merely because the provider returned a different number.

## Optional: Snowflake and Kafka

The default local commands need neither service. For cloud persistence, install the optional connector:

```powershell
uv sync --extra snowflake
```

Set `IKAROS_DB_DRIVER=snowflake` in `.env`, configure the Snowflake account/user and an allowed authentication mechanism, and use `config/database.yaml` for the cloud database, schema and warehouse. Interactive MFA may be required; unattended ingestion should use an approved service identity. `run.check_snowflake_auth` can check the configured connection. Do not store passwords or private keys in version control.

Kafka is a separate, optional live-transport workflow (`uv sync --extra kafka`, `python -m run.ikaros --help`). It is **not part of the candidate-first DuckDB quick start**.

## Developer notes

The primary ingestion path consists of `run.update_all` / `run.backfill_local` → `run.stage_candidates` → stored, validated candidate rows → `run.promote_candidates`. All authoritative price writes go through the promotion modules under `database/`. Database selection and the local path are controlled by `.env`.

```powershell
make test
make lint-check
```

Contributions are welcome, especially reviewed instrument registries, provider test fixtures, and improvements to source availability. Please do not commit secrets, unverified derivative IDs, or fabricated OHLC/settlement values.
