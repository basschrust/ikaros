# Starter reference evidence and maintenance

The checked-in CSVs are small reviewed reference inputs, not downloaded price history.

- WIG20 composition: GPW Benchmark, published 2026-09-03, quarterly adjustment after the 2026-09-18 session, first subsequent session 2026-09-21. https://gpwbenchmark.pl/pub/BENCHMARK/files/PDF/komunikaty_indeksowe/Portfele_WIG20_mWIG40_sWIG80_WIG30_2026_09_03.pdf (page 1). The reference percentages total 100.0000; they use prices of 2026-08-21 and are not recalculated daily. Exchange company labels are mapped to Ikaros's canonical Yahoo-style short tickers, including EBP.WA and MDV.WA (consistent with the existing reference tests). Provider quote availability must be checked live.
- Futures: official GPW October 2026 monthly statistics https://www.gpw.pl/pub/GPW/statystyki/statystyki_miesieczne/202610_GPW.pdf. The official expiries are used rather than the one-day-earlier dates shown by some vendor listings.
- Options: KDPW's December 2025 ISIN announcement https://www.kdpw.pl/uploads/documents/isin/komunikat-nr-18-grudzien-2025-opcje-i-kontrakty.pdf explicitly lists December 2026 calls and puts at the selected strikes. This is registration evidence, not proof of actual trades. The primary contract CSV filename matches existing repository tests; supplementary options bring the total to 20.
- Equity aliases: GPW short codes used by BiznesRadar; they are dated from 2026-09-21. Yahoo-style symbols are created by the existing bootstrap. The PZU Stooq alias `pzu` is also documented in the existing repository’s provider-symbol integration test. Stooq may require an API key; no other Stooq aliases are guessed.

Maintain these inputs with new dated snapshots and exact verified contracts before old reference data becomes stale. Import newer files using `run.import_local_daedalus_inputs --weights-csv ... --contracts-csv ... --write`, after registering any new constituent equities. Do not use a historical snapshot to claim historical composition before its effective date.

## Scope and limitations

This package targets the default WIG20 scenario. It does not create calibration IDs or ensure every listed option trades. The local check requires 252 closes and alignment to the index plus the most recent 20 published GPW sessions, three futures with latest-session closes, two recent FX quotes and at least one usable option. Fewer than 20 usable options is printed as a warning, matching the existing Daedalus local validation minimum of one option with four sessions. Daedalus still owns final validation. The existing GPW calendar helper supports 2026/2027 only; maintainers must extend it before a new year is needed. The 450-day download may span 2025; the historical checks use observed index sessions, not an invented calendar for 2025.

The merged master also has a test for a private PZU repair CSV (`data/pzu_verified_gap_2026_10_14_to_11_20.csv`), which was not present in the GitHub tree. This package does not invent the missing historical rows. Restore the verified original fixture separately if needed for that test.
