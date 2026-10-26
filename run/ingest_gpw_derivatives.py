"""Import independently sourced GPW derivative contract OHLCV from CSV into Snowflake.

Read-only dry-run by default; --write registers contracts and upserts observations.
Inputs MUST be real individual expiry contracts, not continuous synthetic tickers.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from config.config import load_config
from database.repositories.factory import repository_from_config
from ingestion.derivatives import validate_contracts, validate_prices

log = logging.getLogger(__name__)

DDL = (
    '''CREATE TABLE IF NOT EXISTS "DERIVATIVE_CONTRACTS" (
        "CONTRACT_ID" VARCHAR, "SYMBOL" VARCHAR, "INSTRUMENT_TYPE" VARCHAR,
        "UNDERLYING_SYMBOL" VARCHAR, "EXPIRY_DATE" DATE, "CURRENCY" VARCHAR,
        "MULTIPLIER" FLOAT, "STRIKE" FLOAT, "OPTION_SIDE" VARCHAR,
        "SOURCE" VARCHAR, "CREATED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())''',
    '''CREATE TABLE IF NOT EXISTS "DERIVATIVE_PRICES" (
        "CONTRACT_ID" VARCHAR, "DATE" DATE, "OPEN" FLOAT, "HIGH" FLOAT,
        "LOW" FLOAT, "CLOSE" FLOAT, "VOLUME" FLOAT,
        "OPEN_INTEREST" FLOAT, "SETTLEMENT" FLOAT, "SOURCE" VARCHAR,
        "INGESTED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())''',
)



def check_references(
    connection,
    contracts: pd.DataFrame,
    *,
    write: bool,
) -> None:
    """Validate existing contracts and register missing ones."""

    def comparable(value):
        return None if pd.isna(value) else value

    with connection.cursor() as cur:
        cur.execute(
            'SELECT "CONTRACT_ID", "SYMBOL", "INSTRUMENT_TYPE", '
            '"UNDERLYING_SYMBOL", "EXPIRY_DATE", "CURRENCY", '
            '"MULTIPLIER", "STRIKE", "OPTION_SIDE", "SOURCE" '
            'FROM "DERIVATIVE_CONTRACTS"'
        )

        current = {row[0]: row[1:] for row in cur.fetchall()}

        for row in contracts.itertuples(index=False, name=None):
            contract_id, *attributes = row

            existing = current.get(contract_id)

            if existing is not None:
                if (
                    tuple(map(comparable, existing))
                    != tuple(map(comparable, attributes))
                ):
                    raise ValueError(
                        f"Contract specification conflict: {contract_id}"
                    )
                continue

            if not write:
                log.info("NEW CONTRACT %s", contract_id)
                continue

            cur.execute(
                'INSERT INTO "DERIVATIVE_CONTRACTS" '
                '("CONTRACT_ID", "SYMBOL", "INSTRUMENT_TYPE", '
                '"UNDERLYING_SYMBOL", "EXPIRY_DATE", "CURRENCY", '
                '"MULTIPLIER", "STRIKE", "OPTION_SIDE", "SOURCE") '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                tuple(map(comparable, row)),
            )

            log.info("REGISTERED CONTRACT %s", contract_id)


def persist_prices(connection, frame: pd.DataFrame) -> None:
    """Stage + MERGE. Existing prices are refreshed on later source revisions."""
    if frame.empty:
        return
    sql = '''MERGE INTO "DERIVATIVE_PRICES" AS t USING "IKAROS_DERIVATIVE_STAGE" AS s
      ON t."CONTRACT_ID" = s."CONTRACT_ID" AND t."DATE" = s."DATE"
      WHEN MATCHED THEN UPDATE SET t."OPEN"=s."OPEN",t."HIGH"=s."HIGH",t."LOW"=s."LOW",t."CLOSE"=s."CLOSE",t."VOLUME"=s."VOLUME",t."OPEN_INTEREST"=s."OPEN_INTEREST",t."SETTLEMENT"=s."SETTLEMENT",t."SOURCE"=s."SOURCE",t."INGESTED_AT"=CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT ("CONTRACT_ID","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME","OPEN_INTEREST","SETTLEMENT","SOURCE") VALUES (s."CONTRACT_ID",s."DATE",s."OPEN",s."HIGH",s."LOW",s."CLOSE",s."VOLUME",s."OPEN_INTEREST",s."SETTLEMENT",s."SOURCE")'''
    columns = ["CONTRACT_ID", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "OPEN_INTEREST", "SETTLEMENT", "SOURCE"]
    with connection.cursor() as cur:
        cur.execute('CREATE TEMP TABLE IF NOT EXISTS "IKAROS_DERIVATIVE_STAGE" LIKE "DERIVATIVE_PRICES"')
        try:
            cur.execute("BEGIN")
            cur.execute('DELETE FROM "IKAROS_DERIVATIVE_STAGE"')
            insert = ('INSERT INTO "IKAROS_DERIVATIVE_STAGE" (' + ','.join(f'"{c}"' for c in columns) + ') VALUES (%s,TO_DATE(%s),%s,%s,%s,%s,%s,%s,%s,%s)')
            rows = []
            for rec in frame.itertuples(index=False, name=None):
                rows.append(tuple(rec[0:1]) + (rec[1].isoformat(),) + tuple(None if pd.isna(x) else x for x in rec[2:]))
            cur.executemany(insert, rows)
            cur.execute(sql)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--contracts", type=Path, required=True)
    p.add_argument("--prices", type=Path, required=True)
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    contracts = validate_contracts(pd.read_csv(args.contracts, dtype={"contract_id": "string"}))
    prices = pd.read_csv(args.prices, dtype={"contract_id": "string"})
    prices = validate_prices(prices, set(contracts["contract_id"]))
    log.info("Validated contracts=%d observations=%d write=%s", len(contracts), len(prices), args.write)
    if not args.write:
        log.info("DRY RUN complete. No database accessed or modified.")
        return
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("database.driver must be snowflake")
    with repository_from_config(config) as repo:
        with repo.connection.cursor() as cur:
            for ddl in DDL:
                cur.execute(ddl)
        check_references(repo.connection, contracts, write=True)
        persist_prices(repo.connection, prices)
    log.info("Completed Snowflake MERGE")


if __name__ == "__main__":
    main()
