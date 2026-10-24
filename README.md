# Ikaros: seed FX currency and pair reference data

Copy `run/seed_fx_reference.py` and `tests/test_seed_fx_reference.py` into your Ikaros checkout. Nothing is committed to GitHub.

Requires an existing working Snowflake connector, the existing `config/currencies.yaml`, and `config/database.yaml` selecting `snowflake`. Credentials stay in environment variables.

## Dry run

```powershell
uv run python -m run.seed_fx_reference --symbols USDPLN=X EURUSD=X
```

## Write missing records, leaving EURPLN unchanged

```powershell
uv run python -m run.seed_fx_reference --symbols USDPLN=X EURUSD=X --write
```

## Optional: seed every pair defined in YAML

```powershell
uv run python -m run.seed_fx_reference --all
uv run python -m run.seed_fx_reference --all --write
```

Warning: Review `config/currencies.yaml` before seeding all pairs; an existing value `J PYCZK=X` appears to contain an accidental space.

Then retry the same backfill command that failed previously.

### Verify with Snowsight

```sql
SELECT p.SYMBOL, b.CODE AS BASE_CURRENCY, q.CODE AS QUOTE_CURRENCY, p.CURRENCY_PAIR_ID
FROM DAEDALUS.MARKET_DATA.CURRENCY_PAIRS p
JOIN DAEDALUS.MARKET_DATA.CURRENCIES b ON p.BASE_CURRENCY_ID = b.CURRENCY_ID
JOIN DAEDALUS.MARKET_DATA.CURRENCIES q ON p.QUOTE_CURRENCY_ID = q.CURRENCY_ID
WHERE p.SYMBOL IN ('EURPLN=X', 'USDPLN=X', 'EURUSD=X')
ORDER BY p.SYMBOL;
```

The tool uses existing IDs and inserts only missing reference rows in one DML transaction. It does not touch FX_RATES. It assumes no concurrent initializer; MAX(ID)+1 is suitable for this initial development setup, not multi-writer production.
