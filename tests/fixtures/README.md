# Deterministic regression fixtures

These files are test inputs, not an instruction to populate the production database.

- `derivative_approval_review.csv`: original reviewed 334-row inventory (332 approved), recovered from the project owner's previously saved file dated 2026-11-05. It is intentionally historical, including rejected rows, not a current derivative listing.
- `pzu_verified_gap_2026_10_14_to_11_20.csv`: 27 actual historical OHLCV rows, transcribed from https://www.biznesradar.pl/notowania-historyczne/PZU on 2026-11-27; no row for the 11 November closure.
- `wig20_eod_2026_10.csv`: eight actual index OHLC rows, 19–28 October 2026, from https://www.biznesradar.pl/notowania-historyczne/WIG20, retrieved 2026-11-27. No share volume is inferred for an index.
- The index weight tests now use the official, already-versioned `data/wig20_gpw_official_sept2026.csv`, rather than the missing October snapshot. The test mutations use CSV fields, not brittle text replacement.
