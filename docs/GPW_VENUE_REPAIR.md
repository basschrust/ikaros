# Repair missing GPW venues after the coverage refactor

Target: Ikaros `feature/clean_s`, **after** applying the earlier `VENUE` / `INSTRUMENT_TYPE` refactor. The earlier refactor added `DERIVATIVE_CONTRACTS.VENUE` and `run.reclassify_coverage`.

## Why the CSV had blank option venues

The command `python -m run.prepare_daedalus --reference-only` reconciles only the starter manifest (four WIG20 futures and 20 options). The full-history backfill can register hundreds of separately listed options. Those older registry rows were not necessarily in the starter CSV and therefore kept `VENUE=NULL`.

`run.reclassify_coverage` correctly reads metadata from `DERIVATIVE_CONTRACTS`, so it emitted an empty venue for the larger universe. **This was an omission in the preceding installation instructions**, not evidence that the option trades occurred on a different exchange.

## Quickest existing recovery (online, no code changes)

```powershell
python -m run.backfill_wig20_universe --inventory-only --write
python -m run.reclassify_coverage --output output/wig20_history/coverage_venue_repaired.csv --write
```

This works when the current listing feeds can rediscover all contracts in the report. It may miss older, unlisted, or temporarily unavailable contracts. The reclassifier refuses to overwrite an existing report, hence the new `--output` name.

## Offline repair from retained discovery evidence (this package)

The new command is appropriate when you *already have* `output/wig20_history/discovered_contracts.csv` or saved contract `SOURCE` metadata. It uses no HTTP calls and changes **only** `DERIVATIVE_CONTRACTS.VENUE`.

1. Extract the ZIP into the existing Ikaros checkout, preserving paths. Do not copy any Python file into the repository root.
2. Close DBeaver if it holds `ikaros.duckdb` open, and activate your normal Ikaros environment.
3. Preview, then apply:

```powershell
python -m run.reconcile_gpw_venues
python -m run.reconcile_gpw_venues --write
```

4. **Regenerate the CSV**. The previously created report is a static file; updating the database does not modify it:

```powershell
python -m run.reclassify_coverage --output output/wig20_history/coverage_venue_repaired.csv --write
```

The old `coverage.csv` and `coverage_venue.csv` are preserved. If `coverage_venue_repaired.csv` already exists, use a different name. To point at a different archived discovery file, use `--manifest data/saved_discovered_contracts.csv`.

## Evidence and safety boundaries

- The code verifies **the exact GPW option/futures listing URLs** used by `providers/derivatives/wig20_listings.py` together with its `STANDARD_DERIVED` provenance token. This token is inserted by the exact-symbol discovery pipeline. A bare listing URL is insufficient.
- It also checks the contract ID, symbol, instrument type, underlying and expiration against `DERIVATIVE_CONTRACTS` before trusting a retained discovery row. Existing strikes and option sides are checked for consistency when present.
- It never infers GPW from an `OW20...` prefix, from `WIG20` alone, from the `DATASET` column, or from the exchange of another instrument.
- A conflicting existing, non-null foreign venue causes a failure rather than an overwrite. Unverifiable `NULL` venues remain `NULL` and are listed explicitly.
- A dry run performs no metadata update. `--write` uses a transaction and checks the number of updated rows.
- No derivatives are created or removed; no prices, candidates, mappings, weights or instrument identifiers are changed. This does not solve possible contract-ID collisions across different exchanges.
- The current `coverage.csv` cannot be retroactively reclassified by running `run.backfill_wig20_universe --inventory-only`; you must run `run.reclassify_coverage` again.

### Database audit query

```sql
SELECT "INSTRUMENT_TYPE", COALESCE(NULLIF(TRIM("VENUE"), ''), '<MISSING>') AS "VENUE", COUNT(*)
FROM "DERIVATIVE_CONTRACTS"
WHERE "UNDERLYING_SYMBOL"='WIG20'
GROUP BY 1,2
ORDER BY 1,2;
```

You should see GPW for verified contracts, and `<MISSING>` only where there is not enough retained evidence. You should **not** indiscriminately run `UPDATE ... WHERE UNDERLYING_SYMBOL='WIG20'`: that would manufacture venue information.

## Testing

```powershell
python -m pytest -q tests/test_derivative_venue_reconciliation.py
python -m pytest -q tests
python -m ruff check database/derivative_venue_reconciliation.py run/reconcile_gpw_venues.py tests/test_derivative_venue_reconciliation.py
python -m ruff format --check database/derivative_venue_reconciliation.py run/reconcile_gpw_venues.py tests/test_derivative_venue_reconciliation.py
```

The focused offline tests were run in an isolated Python environment. The full Ikaros suite and a live DuckDB migration need verification in your Python 3.12 environment.
