"""Load validated index OHLC snapshots into Snowflake; dry-run unless --write."""
from __future__ import annotations

import argparse
from pathlib import Path
from ingestion.index_prices import read_index_csv

DDL = '''CREATE TABLE IF NOT EXISTS "INDEX_PRICES" (
 "INDEX_SYMBOL" VARCHAR NOT NULL,
 "DATE" DATE NOT NULL,
 "OPEN" FLOAT NOT NULL,
 "HIGH" FLOAT NOT NULL,
 "LOW" FLOAT NOT NULL,
 "CLOSE" FLOAT NOT NULL,
 "SOURCE" VARCHAR NOT NULL,
 "INGESTED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)'''
MERGE = '''MERGE INTO "INDEX_PRICES" t USING
 (SELECT %s AS "INDEX_SYMBOL", TO_DATE(%s) AS "DATE", %s AS "OPEN",
         %s AS "HIGH", %s AS "LOW", %s AS "CLOSE", %s AS "SOURCE") s
 ON t."INDEX_SYMBOL" = s."INDEX_SYMBOL" AND t."DATE" = s."DATE"
 WHEN MATCHED THEN UPDATE SET t."OPEN" = s."OPEN", t."HIGH" = s."HIGH",
   t."LOW" = s."LOW", t."CLOSE" = s."CLOSE", t."SOURCE" = s."SOURCE",
   t."INGESTED_AT" = CURRENT_TIMESTAMP()
 WHEN NOT MATCHED THEN INSERT
   ("INDEX_SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "SOURCE")
 VALUES (s."INDEX_SYMBOL", s."DATE", s."OPEN", s."HIGH", s."LOW", s."CLOSE", s."SOURCE")'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, default=Path('data/wig20_eod_2026_10.csv'))
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    rows = read_index_csv(args.csv)
    print(f'Validated {len(rows)} rows; symbols={sorted({r[0] for r in rows})}; '
          f'dates={rows[0][1]}..{rows[-1][1]}')
    if not args.write:
        print('DRY RUN: rerun with --write to persist')
        return

    from config.config import load_config
    from database.repositories.factory import repository_from_config

    config = load_config('database.yaml')
    if config.get('database', {}).get('driver', '').lower() != 'snowflake':
        parser.error('Select database.driver: snowflake')
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            cur.execute(DDL)  # Snowflake DDL auto-commits: intentionally outside transaction.
            try:
                cur.execute('BEGIN')
                for row in rows:
                    cur.execute(MERGE, row)
                cur.execute('COMMIT')
            except Exception:
                cur.execute('ROLLBACK')
                raise
    print(f'Upserted {len(rows)} index quotations into INDEX_PRICES')


if __name__ == '__main__':
    main()
