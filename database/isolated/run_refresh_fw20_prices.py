"""SQL implementations isolated from run.refresh_fw20_prices. Generated from source; review before deploying."""

from __future__ import annotations
import importlib
from typing import Any
from contextlib import closing
from ingestion.fw20_history import CONTRACT

URL = "https://www.biznesradar.pl/notowania-historyczne/{}"

_ORIGIN = "run.refresh_fw20_prices"
_PROTECTED = frozenset(
    {
        "_sync_origin_globals",
        "_body_persist",
        "persist",
        "_origin_module",
        "registered",
        "_body_registered",
    }
)


def _sync_origin_globals() -> None:
    origin = importlib.import_module(_ORIGIN)
    for name, value in vars(origin).items():
        if not name.startswith("__") and name not in _PROTECTED:
            globals()[name] = value


def _body_registered(connection):
    with closing(connection.cursor()) as cur:
        cur.execute(
            'SELECT DISTINCT "CONTRACT_ID" FROM "DERIVATIVE_CONTRACTS" WHERE "INSTRUMENT_TYPE" = %s AND "UNDERLYING_SYMBOL" = %s ORDER BY "CONTRACT_ID"',
            ("FUTURE", "WIG20"),
        )
        return [str(row[0]) for row in cur.fetchall() if CONTRACT.fullmatch(str(row[0]))]


def registered(*args: Any, **kwargs: Any) -> Any:
    _sync_origin_globals()
    return _body_registered(*args, **kwargs)


def _body_persist(connection, rows):
    sql = 'MERGE INTO "DERIVATIVE_PRICES" t USING (\n        SELECT %s::VARCHAR AS cid, TO_DATE(%s) AS day,\n               %s::FLOAT AS op, %s::FLOAT AS hi, %s::FLOAT AS lo,\n               %s::FLOAT AS cl, %s::FLOAT AS vol, %s::VARCHAR AS src\n    ) s ON t."CONTRACT_ID" = s.cid AND t."DATE" = s.day\n    WHEN MATCHED AND t."SOURCE" = s.src THEN UPDATE SET\n        t."OPEN"=s.op, t."HIGH"=s.hi, t."LOW"=s.lo,\n        t."CLOSE"=s.cl, t."VOLUME"=s.vol,\n        t."INGESTED_AT"=CURRENT_TIMESTAMP()\n    WHEN NOT MATCHED THEN INSERT\n        ("CONTRACT_ID","DATE","OPEN","HIGH","LOW","CLOSE","VOLUME",\n         "OPEN_INTEREST","SETTLEMENT","SOURCE")\n        VALUES (s.cid,s.day,s.op,s.hi,s.lo,s.cl,s.vol,NULL,NULL,s.src)'
    with closing(connection.cursor()) as cur:
        try:
            cur.execute("BEGIN")
            for r in rows:
                cur.execute(
                    sql,
                    (
                        r["contract_id"],
                        r["date"].isoformat(),
                        r["open"],
                        r["high"],
                        r["low"],
                        r["close"],
                        r["volume"],
                        r["source"],
                    ),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def persist(*args: Any, **kwargs: Any) -> Any:
    _sync_origin_globals()
    return _body_persist(*args, **kwargs)
