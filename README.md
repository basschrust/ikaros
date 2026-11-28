# Ikaros 🪽

**Market data for [Daedalus](https://github.com/basschrust/daedalus).** Ikaros downloads real market observations, checks them, and stores accepted data in a local DuckDB database. Daedalus uses that data to prepare and run simulations.

## Quick start — Windows

**For the standard local setup, you do not need to install Python, Snowflake, Docker, Kafka, or Make yourself.** You need Windows 10/11 and an internet connection. If `uv` is missing, the setup script can install it using Windows Package Manager (`winget`).

### 1. Put the projects next to each other

Download or clone **both** repositories. Place their folders in the **same parent directory**, like this:

```text
C:\Projects\
├── Daedalus\
└── Ikaros\
```

The `Daedalus` and `Ikaros` folders must be **siblings**—neither folder should be inside the other. If you downloaded ZIP files from GitHub, extract each project into the corresponding folder.

### 2. Run the Ikaros setup

Open the **Ikaros** folder in Windows File Explorer and double-click:

```text
setup-ikaros.cmd
```

The setup prepares the Ikaros Python environment, creates the local database, registers the bundled WIG20 instruments, downloads historical observations, and checks whether the data are ready for Daedalus.

The first run downloads substantial market data and may take some time. Keep the internet connection active. If Windows asks to approve the installation of `uv`, approve it.

### 3. Check the result

A successful setup ends with:

```text
LOCAL WIG20 CHECK PASSED
```

You can now open **Daedalus** and follow its README to prepare a simulation using the local DuckDB source.

If you see **`NOT READY`** or **`SETUP INCOMPLETE`**, read the messages printed above it. They identify missing prices, unavailable providers, or configuration problems. Previously accepted observations remain stored. Fix the issue and run `setup-ikaros.cmd` again.

**To refresh market data later, run `setup-ikaros.cmd` again.** It reuses accepted observations and looks for missing data; it does not start Kafka. Do not run two setup copies at once. If DBeaver has the database open, disconnect it before running setup.

## Where are the files?

With the folder layout shown above, Ikaros stores its market database here:

```text
C:\Projects\Daedalus\input\market_data\ikaros.duckdb
```

The setup prints the actual database path. It also records it in Ikaros's local `.env` file. Existing `.env` settings are preserved, so an already configured installation may use a different path.

Reports from the extended WIG20 historical backfill are stored in:

```text
Ikaros\output\wig20_history\
    coverage.csv
    discovered_contracts.csv
    summary.json
```

`coverage.csv` shows instrument identity, accepted prices and gaps. Derivative contracts can be listed without having traded on every session; missing option prices are not invented.

If you **deliberately** want another database location, change `IKAROS_MARKET_DATA_DIR` in `.env` before setup. Specify the **directory**, not the `ikaros.duckdb` filename. Never commit `.env` or credentials to Git.

## Command-line alternatives

These commands are optional. The **Quick start** above requires none of them.

From PowerShell inside the Ikaros folder, you can run the same setup with:

```powershell
.\setup-ikaros.cmd
```

To prepare **only** the Python environment (no market-data download):

```powershell
.\setup-ikaros.cmd -EnvironmentOnly
```

For Ikaros Python commands, first activate its environment:

```powershell
.\Ikaros\Scripts\Activate.ps1
```

If PowerShell activation is restricted, use `.\Ikaros\Scripts\python.exe` in place of `python` in the examples below. The commands assume you are working in the **Ikaros** repository root.

| Task | Command |
| --- | --- |
| Check the existing database without downloading | `python -m run.prepare_daedalus --check-only` |
| Register bundled reference instruments without downloading prices | `python -m run.prepare_daedalus --reference-only` |
| Refresh local WIG20 starter data | `python -m run.prepare_daedalus` |
| Download starter history from a specified date | `python -m run.prepare_daedalus --start 2025-01-01` |
| Update all registered instrument datasets | `python -m run.update_all --write` |
| Backfill all registered instrument datasets | `python -m run.backfill_local --write` |

For `run.update_all` and `run.backfill_local`, omit `--write` to preview the work without publishing new observations. The standard setup only needs the WIG20 starter workflow; the generic commands can include additional registered markets.

When Ikaros reports `LOCAL WIG20 CHECK PASSED`, run Daedalus from **Daedalus's own Python environment**, following its README. For example:

```powershell
python -m run.simulation_main --dataset wig20 --source duckdb --prepare-only
```

Ikaros checks source-data coverage; Daedalus performs its own scenario and risk-calibration checks.

## What the standard setup includes

The bundled reference data include:

- **WIG20:** twenty companies from the reviewed GPW Benchmark adjustment effective 21 September 2026. The published weights use August 2026 reference prices; they are not current live weights.
- **Futures:** four registered WIG20 futures series with reviewed expiries and multipliers.
- **Options:** twenty registered December 2026 WIG20 options, with strike, call/put type and multiplier. Registration does not guarantee that a contract has historical trades.
- **FX:** EUR/PLN and USD/PLN market observations and existing configured reference-rate series.

By default, the starter attempts to collect approximately 450 calendar days of equity/index history and checks the availability of 252 market sessions. Derivative retrieval focuses on recent sessions. Actual accepted prices come from the normal stage-and-promote process, never from fabricated data.

**Reference data have dates.** The September 2026 WIG20 constituents and December 2026 contracts are not perpetual definitions of the market. Maintain the bundled references when index membership or listed contracts change. The provenance and maintenance rules are in [Starter reference evidence](docs/DAEDALUS_STARTER.md).

## Extended WIG20 historical research

The normal Quick start is aimed at the standard Daedalus input. To download a broader history for the current WIG20 basket and discovered WIG20 futures/options, run:

```powershell
python -m run.backfill_wig20_universe --write
```

The command discovers exact listed derivative symbols from supported source listings and includes already registered unexpired series. It starts at **2025-01-01** by default and works through the most recent completed GPW session. It does not claim to reconstruct historical index membership before the reviewed snapshot date.

Useful variants:

```powershell
# Inspect the live derivative inventory; do not write to the database
python -m run.backfill_wig20_universe

# Register discovered contract metadata without downloading historical prices
python -m run.backfill_wig20_universe --inventory-only --write

# Request a specific historical window
python -m run.backfill_wig20_universe --start 2025-01-01 --through 2026-11-26 --write
```

The requested date range must be covered by the maintained GPW calendar. Existing accepted observations and reviewed provider mappings are preserved. Missing stock/index sessions can be retried; derivative histories can be sparse, even if a listing is valid. Source failures, missing futures closes and options without accepted history appear in `coverage.csv` and `summary.json`.

The reports distinguish a successful provider query returning no observations from a provider error or a rejected observation. Nonzero exit status means unresolved issues, not that stored data were discarded. Newer coverage reports distinguish **venue** (such as GPW) from **instrument type** (such as INDEX, EQUITY, FUTURE or OPTION); missing reference classifications must be reviewed rather than guessed.

## Adding other instruments and markets

To add stocks from another exchange, supply a reviewed universe CSV under `data/` and run the local setup. Supported market CSV names include `gpw_stocks.csv`, `us_sp500.csv` and `europe_pse.csv`. The database stores each stock's venue separately from its instrument class.

Derivative contracts must be registered with exact listed symbols, expiry dates and reviewed contract specifications. A future or option is a particular expiring contract, not simply its underlying. You can use the format of `data/derivative_contracts.example.csv` or the full reference importer. Keep venue metadata explicit; **do not infer the exchange solely from the contract's symbol**.

For regular incremental ingestion of registered instruments:

```powershell
python -m run.update_all --write
```

For historical backfill of all registered instruments:

```powershell
python -m run.backfill_local --write
```

Provider outages, rate limits and coverage gaps are reported. Existing accepted observations remain intact when a new candidate conflicts with an authoritative price.

## Optional Snowflake and Kafka

**You do not need either service for the standard local Quick start.** This section is for advanced installations.

The setup launcher creates a project-local Python environment named `Ikaros`. In a new PowerShell terminal, activate it and install optional packages using `uv`. If invoking `uv` outside the launcher, set `UV_PROJECT_ENVIRONMENT` for that terminal so it targets the same environment:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "Ikaros"
uv sync --locked --inexact --extra snowflake
```

To use Snowflake, set `IKAROS_DB_DRIVER=snowflake` in `.env`, configure account/authentication settings and `config/database.yaml`, then check the connection with `python -m run.check_snowflake_auth`. Do not commit credentials.

For Kafka, install its optional dependency group as well and see the operator CLI:

```powershell
uv sync --locked --inexact --extra kafka --extra snowflake
python -m run.ikaros --help
```

Kafka requires a configured broker and its own deployment prerequisites. These cloud/live workflows are separate from the single-laptop local setup.

## Development and tests

From the Ikaros folder:

```powershell
.\setup-ikaros.cmd -EnvironmentOnly -Developer
```

This provisions the named `Ikaros` Python environment with optional connectors and development tools. It does not download market history or remove any existing `.venv`.

For PyCharm, select the existing interpreter `Ikaros\Scripts\python.exe` inside the repository folder. Only after confirming the new interpreter works should you remove an older `.venv`; do not rename a virtual environment in place.

Run checks from the repository root:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -q tests
```

If Make is available, `make lint-check` and `make test` are equivalent shortcuts. Price publication remains restricted to the candidate-promotion modules under `database/`.
