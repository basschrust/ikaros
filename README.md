# Ikaros: basic FX → Snowflake smoke test

Copy `run/smoke_fx_snowflake.py` to `run/` in your local Ikaros project.
It uses your previously prepared `database/snowflake_storage.py`, `database/repositories/factory.py`, `config/database.yaml` and current `run/ingest_fx_rates.py`.

1. Install/sync environment: `uv sync --extra snowflake --extra dev` (adjust extras if your local `pyproject.toml` uses different names).
2. Set `database.driver: snowflake` in `config/database.yaml`.
3. Configure `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` as environment variables (not in Git). Use a role with USAGE on database/warehouse and CREATE SCHEMA or schema USAGE plus CREATE TABLE privileges.
4. Ensure Snowflake tables `CURRENCIES` and `CURRENCY_PAIRS` have been seeded. The runner resolves `currency_pair_id` from `CURRENCY_PAIRS` and *will not* create arbitrary IDs.
5. From project root run:

```powershell
uv run python -m run.smoke_fx_snowflake --symbol 'EURPLN=X' --start 2026-10-19 --end 2026-10-22
uv run python -m run.smoke_fx_snowflake --symbol 'EURPLN=X' --start 2026-10-19 --end 2026-10-22 --write
```

The first command is dry-run. The second inserts only dates absent at the time of the preflight read and verifies those dates afterward. It is **not concurrency-safe or truly idempotent**: parallel runs can race. The current repository's `save_fx_rates` is append-only. Do not run concurrent writers yet. The next stage should use Snowflake `MERGE` keyed by `(currency_pair_id, date)`.

If no currency pairs are configured in Snowflake, seed `CURRENCIES` / `CURRENCY_PAIRS` first using `run/fill_currencies.py` after switching it to `repository_from_config(load_config('database.yaml'))`. That loader uses the currency configuration and generates IDs; run only once until idempotent reference-data upserts are implemented. Avoid accidentally seeding an existing table twice.

Verify directly in Snowsight:

```sql
SELECT SYMBOL, CURRENCY_PAIR_ID, DATE, CLOSE, INGESTED_AT
FROM DAEDALUS.MARKET_DATA.FX_RATES
WHERE SYMBOL = 'EURPLN=X'
ORDER BY DATE DESC
LIMIT 20;
```

**Note:** The public GitHub `master` snapshot inspected on 2026-10-23 does not yet expose the connector files. This package assumes your locally copied connector bundle is present. No GitHub files were changed or any Snowflake connection attempted here.
