# Ikaros 🪽

**Market data for [Daedalus](https://github.com/basschrust/daedalus)**. Ikaros retrieves real observations, validates candidates and publishes accepted market data to a local DuckDB or optional Snowflake database.

## Windows: start here

**No Snowflake, Docker, Kafka, Make or separate Python installation required.** You need internet access and Windows 10/11 with Windows Package Manager (winget) or an existing [uv installation](https://docs.astral.sh/uv/getting-started/installation/).

1. Download Ikaros using GitHub's **Code → Download ZIP**, extract the folder, or clone it with Git. For automatic detection, put the Ikaros and Daedalus folders next to each other (names `ikaros` and `daedalus` or `pymarketsim`; GitHub ZIP names ending in `-master` or `-main` also work).
2. Open the Ikaros folder and double-click **`setup-ikaros.cmd`**. Approve the uv installation if prompted. The launcher provisions Python 3.12 in the project-local `Ikaros/` environment, installs the locked dependencies, seeds the reviewed WIG20 reference data, downloads historical prices and checks coverage.
3. When the window reports `LOCAL WIG20 CHECK PASSED`, run Daedalus's local source preparation. If it reports `NOT READY`, read the missing-data messages and double-click setup again after resolving the issue. No CSV editing or database GUI is needed for the bundled starter universe.

First download can be substantial. Subsequent runs reuse accepted observations and target gaps. The same `setup-ikaros.cmd` (or `start-ikaros.cmd`) refreshes the database; it does not start Kafka. Do not run two copies simultaneously. Close DBeaver or other processes holding the database open first.

### Where is my database?

On first use, if exactly one sibling Daedalus checkout is detected, Ikaros writes directly to `Daedalus/input/market_data/ikaros.duckdb`. Otherwise it writes to `output/market_data/ikaros.duckdb` inside Ikaros. The actual absolute path is printed and saved in `.env`. Existing `.env` settings are preserved. For a different location, edit `IKAROS_MARKET_DATA_DIR` in `.env` before running setup. Select the directory, not the database filename. Never commit `.env` or credentials.

## PowerShell / command line alternative

From the Ikaros root, the launcher also works from PowerShell:

```powershell
.\setup-ikaros.cmd
```

To create or update just the named Python environment without fetching market data:

```powershell
.\setup-ikaros.cmd -EnvironmentOnly
```

The launcher sets `UV_PROJECT_ENVIRONMENT=Ikaros` only for its own process. For manual uv commands in a new terminal, set `$env:UV_PROJECT_ENVIRONMENT = "Ikaros"` first. Do not set it globally for other projects. Activate the environment with `.\Ikaros\Scripts\Activate.ps1`, or run `.\Ikaros\Scripts\python.exe` directly if activation is blocked. The commands below assume activation.

Useful alternatives:

```powershell
# Check existing database without downloading or seeding
python -m run.prepare_daedalus --check-only
# Reference data only, no prices
python -m run.prepare_daedalus --reference-only
# Explicit earlier history
python -m run.prepare_daedalus --start 2025-01-01
```

For an initial custom location, pass `--market-data-dir C:/projects/Daedalus/input/market_data`. Existing conflicting settings are rejected, not silently redirected.

In the current Daedalus checkout, use its own Python environment and local source option (see Daedalus README and CLI help):

```powershell
python -m run.simulation_main --dataset wig20 --source duckdb --prepare-only
```

Ikaros checks data coverage; Daedalus performs the final scenario and calibration validation. Ikaros does not create risk calibrations or install Daedalus itself.

## What the starter includes

- 20 WIG20 constituents from the official GPW Benchmark quarterly adjustment effective for trading on 21 September 2026. Published percentage weights are based on 21 August 2026 data, not today's live weights.
- Four explicitly listed WIG20 futures and 20 explicitly registered December 2026 WIG20 options, with expiry, multiplier, strike and option side; provenance in CSVs. A listed option need not have traded, and actual price availability can be lower than the requested 20.
- EUR/PLN and USD/PLN market FX, plus the reference pairs/rate series already seeded by Ikaros.
- Initial equity/index history starts 450 calendar days before the last completed GPW session by default, with a target of 252 common sessions. Derivative retrieval uses up to 60 recent calendar days. All actual prices follow the candidate-first stage/promote flow; none are bundled or invented.

The snapshot and derivative manifest are **dated**, not a perpetual live listing. When the index changes or contracts expire, maintainers must publish refreshed, reviewed reference inputs or import them using `run.import_local_daedalus_inputs`. The readiness command reports missing live derivatives; it does not manufacture replacement contracts. See [starter reference provenance](docs/DAEDALUS_STARTER.md).

## Other markets and advanced workflows

For extra GPW/foreign equity universes, place reviewed CSV files in `data/` (e.g. `gpw_stocks.csv`, `us_sp500.csv`, `europe_pse.csv`) and run `python -m run.setup_local_duckdb --write`. Personal files remain ignored by Git. To register extra derivatives, use the reviewed manifest format in `data/derivative_contracts.example.csv` or the full metadata importer.

```powershell
# Historical data for all registered instruments from 2025-01-01
python -m run.backfill_local --write
# Incremental update of all registered domains through yesterday in Warsaw
python -m run.update_all --write
# Preview either operation by omitting --write
```

These generic commands are separate from the targeted WIG20 starter. Data-provider outages, rate limits and gaps are reported; existing accepted observations are preserved. For derivative contract registration, use exact exchange-listed IDs and verified metadata. No prices are generated to fill missing observations.

### Optional Snowflake and Kafka

For Snowflake, after setting `UV_PROJECT_ENVIRONMENT` as above, install `uv sync --locked --inexact --extra snowflake`, set `IKAROS_DB_DRIVER=snowflake` in `.env`, configure credentials and `config/database.yaml`, and check `python -m run.check_snowflake_auth`. For Kafka install `uv sync --locked --inexact --extra kafka --extra snowflake` and use `python -m run.ikaros --help`, `setup`, `start` and `status` as appropriate. These cloud/live workflows require their own broker, credentials and reference data; they are not required for local simulations.

## Development and migration from `.venv`

From the Ikaros root in PowerShell:

```powershell
.\setup-ikaros.cmd -EnvironmentOnly -Developer
```

This provisions the named environment with all optional connector packages and development tools from the lockfile. It does not connect to Snowflake or Kafka, download market data, or remove the old `.venv`.

For PyCharm, choose **Settings → Project → Python Interpreter → Add Interpreter → Add Local Interpreter → Existing environment**, then select `Ikaros\Scripts\python.exe` under the project root. Close and reopen the terminal. The prompt should show `(Ikaros)`. Only after verifying the new interpreter and tests, remove the old `.venv` if desired. Never rename an existing virtual environment in place.

```powershell
make format
make lint-check
make test
```

Make invokes the named interpreter directly, without activation or uv on PATH. If Make is not installed, activate the environment and use `python -m pytest -q tests`, `python -m ruff check .` and `python -m ruff format --check .`. The authoritative price write boundary remains the existing candidate promotion modules under `database/`.
