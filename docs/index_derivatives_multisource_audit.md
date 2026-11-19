# WIG20 index and GPW derivatives: multi-source gap assessment (2026-11-18)

Read-only inspection of `basschrust/ikaros`, branch `feature/clean_ge`.

## Confirmed current behavior

| Dataset | Invoked by incremental updater | Current source | Target | Multi-source candidates? |
| --- | --- | --- | --- | --- |
| GPW stocks | `run.ingest_gpw_prices` | Yahoo/OpenBB, Stooq (access dependent), BiznesRadar | `PRICES` | Yes, `PRICES_CANDIDATES` |
| WIG20 index | `run.refresh_index_prices` | Yahoo WIG20.WA daily with Yahoo hourly fallback | `INDEX_PRICES` | No |
| Registered GPW futures/options | `run.backfill_derivative_prices` | BiznesRadar HTML history | `DERIVATIVE_PRICES` | No |
| Legacy FW20 | `run.refresh_fw20_prices` | BiznesRadar HTML history | `DERIVATIVE_PRICES` | No |

Do not interpret Yahoo daily + hourly as two independent providers.
Do not calculate official index volume from stock volumes: WIG20 is an index; providers may expose turnover rather than number of shares.
Do not use a generic continuous-futures history to populate individually identified FW20 contract series.

## Recommended immediate implementation order

1. **Index:** establish provider mapping for WIG20 index (e.g., Yahoo `WIG20.WA`, reviewed BiznesRadar `WIG20`) and a staging table `INDEX_PRICE_CANDIDATES` with integer `INDEX_CANDIDATE_ID`, index symbol, date, provider ID, OHLC, turnover/volume semantics, validation status, and content hash. Add explicit validation for index price units and source date. Keep existing `INDEX_PRICES` as the selected-authoritative table. Ensure single valid provider can promote a complete index candle.
2. **Derivatives:** build `DERIVATIVE_PROVIDER_SYMBOLS` mapping actual `CONTRACT_ID` + `PROVIDER_ID` + provider contract symbol + validity. Use a separate `DERIVATIVE_PRICE_CANDIDATES` table with integer primary key and content hash. Validate actual listed contract ID, expiry, instrument type, option strike/side, and completeness of OHLCV. Retain settlement and open interest as explicitly nullable *source-supplied* fields, never synthesize them.
3. **Independent sources:** verify accessible, rights-compliant historical EOD sources before claiming more than one working source. BiznesRadar already supports historical contract pages. Potential *additional* sources require validation; Bankier futures public pages appear to be live quotes, not yet verified as historical contract-level data. Stooq is not reliable without authorized CSV access.
4. **Update routing:** only after the new candidate writers are tested, modify `run.update_all` for `WIG20` and `DERIVATIVES` to point to them. Retain existing jobs as isolated compatibility commands until cutover.
5. **Acceptance:** for each dataset test source outage fallback, revised upstream observations, idempotent replay, no cross-contract matches, preservation of valid existing prices, offline SQL-backed persistence, and read-only live Snowflake query of staged and selected rows.

## Read-only Snowflake checks (Snowsight)

```sql
SELECT "SOURCE", COUNT(*) AS observations,
       MAX(TO_DATE("DATE")) AS last_date
FROM DAEDALUS.MARKET_DATA.INDEX_PRICES
GROUP BY "SOURCE" ORDER BY observations DESC;

SELECT c."INSTRUMENT_TYPE", p."SOURCE", COUNT(*) AS observations,
       MAX(TO_DATE(p."DATE")) AS last_date
FROM DAEDALUS.MARKET_DATA.DERIVATIVE_PRICES p
JOIN DAEDALUS.MARKET_DATA.DERIVATIVE_CONTRACTS c
  ON c."CONTRACT_ID" = p."CONTRACT_ID"
GROUP BY c."INSTRUMENT_TYPE", p."SOURCE"
ORDER BY c."INSTRUMENT_TYPE", observations DESC;
```

**Important:** The candidate-ID migration in this delivery does **not** implement the new index/derivative pipelines. Do not represent those jobs as multi-source after applying it.
