"""Official rate histories: UST, ECB AAA, NY Fed, BoE, ECB €STR, SNB.

Dry-run by default; --write persists to existing Ikaros RATE tables.
"""

from __future__ import annotations

import argparse
from datetime import date
import logging

import requests

from config.config import load_config
from database.repositories.factory import repository_from_config
from ikaros_app.operations import load_environment
from ikaros_app.rates import (
    ECBSpotYieldProvider,
    TreasuryYieldProvider,
    NYFedOvernightProvider,
    selected_sources,
)
from ikaros_app.rates_international import (
    BoERatesProvider,
    ECBESTRProvider,
    SNBPolicyProvider,
    configured_series as international_series,
)

LOG = logging.getLogger(__name__)

SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS "RATE_SERIES" (
       "SERIES_ID" VARCHAR, "CURRENCY" VARCHAR, "SOURCE" VARCHAR,
       "CURVE_TYPE" VARCHAR, "TENOR" VARCHAR, "SOURCE_KEY" VARCHAR,
       "COMPOUNDING" VARCHAR, "DAY_COUNT" VARCHAR,
       "IS_ACTIVE" BOOLEAN DEFAULT TRUE,
       "CREATED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())""",
    """CREATE TABLE IF NOT EXISTS "RATE_OBSERVATIONS" (
       "SERIES_ID" VARCHAR, "DATE" DATE, "RATE" FLOAT,
       "SOURCE" VARCHAR,
       "INGESTED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())""",
)


def persist(connection, series, observations) -> int:
    """Idempotent batch insertion; refuse conflicting references or source overwrite."""
    if not observations:
        return 0
    with connection.cursor() as cur:
        for ddl in SCHEMA_DDL:
            cur.execute(ddl)
        cur.execute("""CREATE TEMP TABLE IF NOT EXISTS "IKAROS_RATE_STAGE" (
            "SERIES_ID" VARCHAR, "DATE" DATE, "RATE" FLOAT, "SOURCE" VARCHAR)""")
        try:
            cur.execute("BEGIN")
            for spec in series.values():
                cur.execute(
                    """SELECT "CURRENCY", "SOURCE", "CURVE_TYPE", "TENOR",
                    "SOURCE_KEY", "COMPOUNDING", "DAY_COUNT"
                    FROM "RATE_SERIES" WHERE "SERIES_ID"=%s""",
                    (spec.series_id,),
                )
                rows = cur.fetchall()
                expected = (
                    spec.currency,
                    spec.source,
                    spec.curve_type,
                    spec.tenor,
                    spec.source_key,
                    spec.compounding,
                    spec.day_count,
                )
                if len(rows) > 1 or (rows and tuple(rows[0]) != expected):
                    raise ValueError(f"Conflicting RATE_SERIES definition: {spec.series_id}")
                if not rows:
                    cur.execute(
                        """INSERT INTO "RATE_SERIES"
                        ("SERIES_ID","CURRENCY","SOURCE","CURVE_TYPE","TENOR",
                         "SOURCE_KEY","COMPOUNDING","DAY_COUNT")
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (spec.series_id, *expected),
                    )
            cur.execute('DELETE FROM "IKAROS_RATE_STAGE"')
            cur.executemany(
                """INSERT INTO "IKAROS_RATE_STAGE"
                ("SERIES_ID","DATE","RATE","SOURCE")
                VALUES (%s,TO_DATE(%s),%s,%s)""",
                [
                    (r.series_id, r.day.isoformat(), r.rate, series[r.series_id].source)
                    for r in observations
                ],
            )
            cur.execute("""SELECT COUNT(*) FROM "IKAROS_RATE_STAGE" s
                JOIN "RATE_OBSERVATIONS" t ON t."SERIES_ID"=s."SERIES_ID"
                   AND t."DATE"=s."DATE"
                WHERE t."SOURCE"<>s."SOURCE" OR t."SOURCE" IS NULL""")
            if cur.fetchone()[0]:
                raise ValueError("Conflicting rate observation source; refusing overwrite")
            cur.execute("""MERGE INTO "RATE_OBSERVATIONS" t USING "IKAROS_RATE_STAGE" s
                ON t."SERIES_ID"=s."SERIES_ID" AND t."DATE"=s."DATE"
                WHEN MATCHED THEN UPDATE SET t."RATE"=s."RATE",
                    t."INGESTED_AT"=CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT ("SERIES_ID","DATE","RATE","SOURCE")
                    VALUES (s."SERIES_ID",s."DATE",s."RATE",s."SOURCE")""")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return len(observations)


def select_series(series_ids: list[str] | None):
    """Combine both catalogs while keeping the legacy rates module unchanged."""
    existing, _ = selected_sources(None)
    extended = international_series()
    overlap = set(existing) & set(extended)
    if overlap:
        raise ValueError(f"Duplicate configured rate IDs: {sorted(overlap)}")
    catalog = {**existing, **extended}
    wanted = set(series_ids) if series_ids is not None else set(catalog)
    unknown = wanted - set(catalog)
    if unknown:
        raise ValueError(f"Unknown rate series: {', '.join(sorted(unknown))}")
    selected = {s: catalog[s] for s in sorted(wanted)}
    return selected, {r.source for r in selected.values()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--series", nargs="+", help="Subset of configured rate-series IDs")
    p.add_argument("--write", action="store_true", help="Upsert official observations in Snowflake")
    args = p.parse_args()
    if args.start > args.end:
        p.error("--start must not follow --end")
    selected, sources = select_series(args.series)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    all_observations = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "IkarosMarketData/1.0"})
        for provider in (
            TreasuryYieldProvider(),
            ECBSpotYieldProvider(),
            NYFedOvernightProvider(),
            BoERatesProvider(),
            ECBESTRProvider(),
            SNBPolicyProvider(),
        ):
            if provider.source not in sources:
                continue
            values = provider.history(session, args.start, args.end)
            chosen = [r for r in values if r.series_id in selected]
            if len({(r.series_id, r.day) for r in chosen}) != len(chosen):
                raise ValueError(f"Duplicate {provider.source} series/date observations")
            all_observations.extend(chosen)
            LOG.info(
                "%s observations=%d series=%d",
                provider.source,
                len(chosen),
                len({r.series_id for r in chosen}),
            )
    LOG.info("Rates observations=%d write=%s", len(all_observations), args.write)
    if not args.write or not all_observations:
        return
    load_environment()
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Select Snowflake in config/database.yaml")
    with repository_from_config(config) as repo:
        count = persist(repo.connection, selected, all_observations)
    LOG.info("MERGED %d rate observations", count)


if __name__ == "__main__":
    main()
