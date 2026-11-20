"""SQL implementations isolated from run.ingest_index_prices. Generated from source; review before deploying."""

from __future__ import annotations
import importlib


_ORIGIN = "run.ingest_index_prices"
_PROTECTED = frozenset(
    {"_sync_origin_globals", "MERGE", "_origin_module", "_body_DDL", "DDL", "_body_MERGE"}
)


def _sync_origin_globals() -> None:
    origin = importlib.import_module(_ORIGIN)
    for name, value in vars(origin).items():
        if not name.startswith("__") and name not in _PROTECTED:
            globals()[name] = value


DDL = 'CREATE TABLE IF NOT EXISTS "INDEX_PRICES" (\n "INDEX_SYMBOL" VARCHAR NOT NULL,\n "DATE" DATE NOT NULL,\n "OPEN" FLOAT NOT NULL,\n "HIGH" FLOAT NOT NULL,\n "LOW" FLOAT NOT NULL,\n "CLOSE" FLOAT NOT NULL,\n "SOURCE" VARCHAR NOT NULL,\n "INGESTED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()\n)'


MERGE = 'MERGE INTO "INDEX_PRICES" t USING\n (SELECT %s AS "INDEX_SYMBOL", TO_DATE(%s) AS "DATE", %s AS "OPEN",\n         %s AS "HIGH", %s AS "LOW", %s AS "CLOSE", %s AS "SOURCE") s\n ON t."INDEX_SYMBOL" = s."INDEX_SYMBOL" AND t."DATE" = s."DATE"\n WHEN MATCHED THEN UPDATE SET t."OPEN" = s."OPEN", t."HIGH" = s."HIGH",\n   t."LOW" = s."LOW", t."CLOSE" = s."CLOSE", t."SOURCE" = s."SOURCE",\n   t."INGESTED_AT" = CURRENT_TIMESTAMP()\n WHEN NOT MATCHED THEN INSERT\n   ("INDEX_SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "SOURCE")\n VALUES (s."INDEX_SYMBOL", s."DATE", s."OPEN", s."HIGH", s."LOW", s."CLOSE", s."SOURCE")'


def _inline_main_52(repo, rows):
    _sync_origin_globals()
    with repo.connection.cursor() as cur:
        cur.execute(DDL)
        try:
            cur.execute("BEGIN")
            for row in rows:
                cur.execute(MERGE, row)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
