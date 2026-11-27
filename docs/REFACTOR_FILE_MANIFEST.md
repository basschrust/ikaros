# Files changed in the coverage refactor

Apply to **Ikaros `feature/clean_s`** by copying the contents of the ZIP into the existing repository, preserving relative paths.

**Updated:**

- `database/daedalus_reference.py` — additive nullable `DERIVATIVE_CONTRACTS.VENUE`
- `database/local_duckdb.py` — invoke additive reference migration on regular startup
- `database/local_bootstrap.py` — accept optional `venue` column in user contract CSV; preserve unknown and refuse conflicts
- `providers/derivatives/wig20_listings.py` — mark exchange-specific WIG20 listing evidence as GPW
- `run/import_local_daedalus_inputs.py` — require GPW venue in reviewed full contract manifests; enrich DB without overwriting conflicts
- `run/backfill_wig20_universe.py` — generate `venue` / `instrument_type` columns, audit unknowns
- `data/derivative_contracts.example.csv` — venue-aware five-column starter example
- `data/wig20_gpw_verified_contracts_2026_10.csv` — reviewed GPW venue recorded for 14 contracts
- `data/wig20_gpw_verified_options_extra.csv` — reviewed GPW venue recorded for 10 options
- `tests/test_local_derivative_setup.py` — new example layout, old personal manifest compatibility

**Added:**

- `database/coverage_identity.py` — authoritative, fail-closed venue/type resolver
- `run/reclassify_coverage.py` — upgrade old reports without reingesting histories
- `tests/test_coverage_identity.py` — offline and optional DuckDB regression coverage
- `docs/COVERAGE_VENUE_REFACTOR.md` — migration, commands, limitations
- `docs/REFACTOR_FILE_MANIFEST.md` — this inventory

There are **no executable scripts in the repository root** and no new Python dependency.
