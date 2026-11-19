# Ikaros — Multi-source route audit and historical gap repair

Target branch: `feature/clean_ge`. This update changes **no database schema**:
it uses the previously installed index, derivative and GPW candidate tables.
There is no data destruction or resynthesis.

## The simple command for the WIG20 hole

Plan-only (prints exactly which dates will be retrieved):

```powershell
uv run --extra snowflake python -m run.update_all --datasets WIG20 --repair-only
```

Write only the missing *interior* WIG20 index sessions:

```powershell
uv run --extra snowflake python -m run.update_all --datasets WIG20 --repair-only --write
```

To catch up the latest index price **and** repair earlier holes, use:

```powershell
uv run --extra snowflake python -m run.update_all --datasets WIG20 --write
```

The default scan covers 20 completed GPW trading sessions, excluding weekends
and GPW holidays. Increase it when needed with `--repair-lookback-sessions 60`
(maximum 100 sessions; the existing GPW calendar only defines 2026 and 2027).

The scanner deliberately compares expected sessions against *actual* `INDEX_PRICES`
rows. A row on 17 or 18 November no longer hides an absent 16 November row.
For the last 2026 sessions, 11 November was an exchange holiday, not a gap.

The date range of each repair job is the tightest possible missing window;
consecutive trading sessions are grouped (even across a weekend).

After `--write`, the WIG20 row set is queried again. If the missing date is
still absent, `run.update_all` exits nonzero rather than reporting success.
`--repair-only` checks interior gaps; standard `--write` also checks trailing
sessions through the requested target.

## All registered GPW market instruments

To repair WIG20 and GPW equity gaps *and* recheck the most recent registered
futures/options history, with no synthetic no-trade candles:

```powershell
uv run --extra snowflake python -m run.update_all --datasets GPW WIG20 DERIVATIVES --repair-only --recheck-derivatives --write
```

`--recheck-derivatives` is intentionally explicit: unlike a continuously
published index, an option or a thin futures series can have days without trades.
Missing contract candles are therefore not classified as errors. The recheck
fetches actual provider observations for registered contracts whose expiry
has not passed the start of the window. Expired series older than the chosen
window require an explicit historical backfill.

`FW20` is now routed to the *same* derivative-candidate pipeline as
`DERIVATIVES`, instead of `run.refresh_fw20_prices`. Selecting both FW20 and
DERIVATIVES together is already deduplicated by the existing planner.

## What "multi-source" currently means (important)

| Dataset | Candidate layer | Verified live adapters |
|---|---|---|
| GPW equities (including 20 WIG20 constituents) | `PRICES_CANDIDATES` | Yahoo Finance, BiznesRadar; Stooq configured but often needs authorization |
| WIG20 index | `INDEX_PRICE_CANDIDATES` | BiznesRadar, Yahoo Finance; Stooq requires working access |
| Registered GPW futures and options | `DERIVATIVE_PRICE_CANDIDATES` | BiznesRadar contract history; **no second verified source yet** |
| Other-country equities (US/PSE/XETRA/PARIS/LSE/BSE) | **Legacy direct importer** | Yahoo feed only |
| FX spot / reference fixes | **Legacy direct importer** | Yahoo and NBP paths; no shared candidate arbitration |
| Official rates/yield series | **Legacy direct importer** | Source-specific central bank/government feeds |

One valid source suffices, exactly as agreed. Separate source records remain
available for arbitration when sources differ. Do not confuse having a generic
candidate table with having two independent working live adapters: derivatives
still only have one, and FX/rates/foreign equities do not have a candidate
pipeline. The updated `run.update_all` logs a **LEGACY SOURCE ROUTES** warning
for those classes; it does not claim universal multi-source coverage.

Do not retire the existing FX/rates/foreign-equity importers until their
source identifiers, quote-unit conventions (particularly London GBp), reference
fixes vs OHLC bars, history calendars, candidate tables and validation tests
have been migrated. Merely redirecting those workers to the GPW adapter would
silently mix incompatible data semantics.

## Preflight / verification

1. Ensure the earlier `run.setup_market_sources --write` has initialized the
   candidate tables and reviewed aliases.
2. Stop overlapping legacy writer instances to avoid concurrent Snowflake
   inserts without enforced PK uniqueness.
3. Run `make format`, `make test`, `make lint-check`.
4. Run the WIG20 plan-only command above. Confirm the reported missing dates.
5. Run the write command. Confirm its status and inspect
   `sql/verify_wig20_gaps_2026.sql` in Snowsight.

`--repair-only` with only FX/rates/non-GPW equity datasets now fails with a
helpful error instead of reporting a misleading success with zero repair jobs.

This update has **not** been exercised against live Snowflake or live external
providers. Its SQL-backed regression tests simulate successful and unsuccessful
backfills, source routing and calendar edge cases. The full suite and Ruff
must be executed in the developer's Python 3.12 environment.
