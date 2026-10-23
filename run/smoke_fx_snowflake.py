"""First live FX ingestion smoke test for Ikaros's Snowflake backend.

Run from the Ikaros project root:
    uv run python -m run.smoke_fx_snowflake --symbol EURPLN=X \
        --start 2026-10-19 --end 2026-10-22

Defaults to dry-run. Pass --write to actually append the sample to Snowflake.
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from run.ingest_fx_rates import fetch_fx_history


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', default='EURPLN=X')
    parser.add_argument('--start', default='2026-10-19')
    parser.add_argument('--end', default='2026-10-22')
    parser.add_argument('--write', action='store_true', help='Actually append data after validating the target')
    args = parser.parse_args()
    if date.fromisoformat(args.start) > date.fromisoformat(args.end):
        parser.error('--start must not be later than --end')

    cfg = load_config('database.yaml')
    if cfg.get('database', {}).get('driver', '').lower() != 'snowflake':
        raise SystemExit('Refusing to continue: set database.driver: snowflake in config/database.yaml')

    with repository_from_config(cfg) as repo:
        pairs = repo.get_currency_pairs()
        matched = pairs[pairs['symbol'].astype(str).str.upper() == args.symbol.upper()]
        if len(matched) != 1:
            raise SystemExit(
                f'Expected exactly one configured currency pair for {args.symbol!r} in Snowflake, '
                f'found {len(matched)}. Seed CURRENCIES and CURRENCY_PAIRS first.'
            )
        pair_id = int(matched.iloc[0]['currency_pair_id'])
        print(f'Connected to {repo.database}.{repo.schema}; {args.symbol} -> pair ID {pair_id}')

        frame = fetch_fx_history(symbol=args.symbol, start_date=args.start, end_date=args.end)
        if frame.empty:
            raise SystemExit('Provider returned no observations; nothing inserted.')
        frame = frame.copy()
        frame['currency_pair_id'] = pair_id
        frame = frame.drop_duplicates(['currency_pair_id', 'date'], keep='last')
        print('Fetched rows:', len(frame))
        print(frame[['symbol', 'date', 'close']].to_string(index=False))

        # Check for pre-existing rows before writing. The current connector is append-only.
        sql = ('SELECT CAST("DATE" AS DATE) AS "DATE" FROM "FX_RATES" '
               'WHERE "CURRENCY_PAIR_ID" = %s AND CAST("DATE" AS DATE) >= %s '
               'AND CAST("DATE" AS DATE) <= %s')
        existing = repo._query(sql, (pair_id, date.fromisoformat(args.start), date.fromisoformat(args.end)))
        existing_dates = set(pd.to_datetime(existing['date']).dt.date) if not existing.empty else set()
        new_frame = frame[~frame['date'].isin(existing_dates)].copy()
        print(f'Already present: {len(frame) - len(new_frame)} days; new: {len(new_frame)} days')
        if new_frame.empty:
            print('No new rows to insert. Smoke test complete.')
            return
        if not args.write:
            print('DRY RUN. Re-run with --write to insert only the missing dates.')
            return

        repo.save_fx_rates(new_frame)
        verified = repo._query(sql, (pair_id, date.fromisoformat(args.start), date.fromisoformat(args.end)))
        verified_dates = set(pd.to_datetime(verified['date']).dt.date) if not verified.empty else set()
        missing = set(new_frame['date']) - verified_dates
        if missing:
            raise RuntimeError(f'Inserted observations missing on readback: {sorted(missing)}')
        print(f'PASS: inserted {len(new_frame)} FX rows and confirmed their dates in Snowflake.')


if __name__ == '__main__':
    main()
