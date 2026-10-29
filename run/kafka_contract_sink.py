"""Consume derivative discovery events, stage candidates, auto-register verified FW20."""

from __future__ import annotations

import argparse
from datetime import date
import logging
import os

from ingestion.contract_discovery import Candidate
from ingestion.fw20_registry import register

LOG = logging.getLogger(__name__)
TOPIC = "ikaros.derivatives.discovery.v1"
DDL = """CREATE TABLE IF NOT EXISTS "DERIVATIVE_CANDIDATES" (
 "SYMBOL" VARCHAR, "INSTRUMENT_TYPE" VARCHAR, "EXPIRY_DATE" DATE,
 "SOURCE" VARCHAR, "SOURCE_AS_OF" VARCHAR,
 "FIRST_SEEN" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
 "LAST_SEEN" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
 "REVIEW_STATUS" VARCHAR DEFAULT 'PENDING')"""
MERGE = """MERGE INTO "DERIVATIVE_CANDIDATES" t USING (
 SELECT %s AS "SYMBOL", %s AS "SOURCE", %s AS "INSTRUMENT_TYPE",
 TO_DATE(%s) AS "EXPIRY_DATE", %s AS "SOURCE_AS_OF"
) s ON t."SYMBOL" = s."SYMBOL" AND t."SOURCE" = s."SOURCE"
WHEN MATCHED THEN UPDATE SET t."LAST_SEEN"=CURRENT_TIMESTAMP(),
 t."SOURCE_AS_OF"=s."SOURCE_AS_OF", t."EXPIRY_DATE"=s."EXPIRY_DATE"
WHEN NOT MATCHED THEN INSERT ("SYMBOL", "SOURCE", "INSTRUMENT_TYPE", "EXPIRY_DATE", "SOURCE_AS_OF")
VALUES (s."SYMBOL", s."SOURCE", s."INSTRUMENT_TYPE", s."EXPIRY_DATE", s."SOURCE_AS_OF")"""


def ingest_message(connection, candidate: Candidate) -> None:
    with connection.cursor() as cur:
        cur.execute(
            MERGE,
            (
                candidate.symbol,
                candidate.source,
                candidate.instrument_type,
                candidate.expiry_date,
                candidate.source_as_of,
            ),
        )
    # WIG20 index futures are the only auto-promoted class. The discovery
    # message itself is the evidence of listing; the contract standard
    # supplies multiplier and expiry rule. All others stay PENDING.
    if (
        candidate.symbol.startswith("FW20")
        and candidate.source == "bankier"
        and candidate.instrument_type == "FUTURE"
    ):
        for symbol, status in register(connection, as_of=date.today(), write=True):
            if symbol == candidate.symbol:
                LOG.info("FW20 REGISTRY %s: %s", symbol, status)
                if status in {"CONFLICT", "DUPLICATE_EXISTING"}:
                    LOG.error("Manual verification required for %s", symbol)


def main():
    from confluent_kafka import Consumer, KafkaException
    from config.config import load_config
    from database.repositories.factory import repository_from_config
    from run.ingest_gpw_derivatives import DDL as DERIVATIVE_DDL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-messages", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    consumer = Consumer(
        {
            "bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "127.0.0.1:9092"),
            "group.id": "ikaros-derivative-discovery-v1",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    processed = 0
    try:
        with repository_from_config(load_config("database.yaml")) as repo:
            with repo.connection.cursor() as cur:
                cur.execute(DDL)
                cur.execute(DERIVATIVE_DDL[0])
            # Catch up already-discovered series on sink startup.
            for symbol, status in register(repo.connection, as_of=date.today(), write=True):
                LOG.info("FW20 STARTUP %s: %s", symbol, status)
            consumer.subscribe([TOPIC])
            while args.max_messages == 0 or processed < args.max_messages:
                message = consumer.poll(2.0)
                if message is None:
                    continue
                if message.error():
                    raise KafkaException(message.error())
                candidate = Candidate.decode(message.value())
                if message.key() != candidate.symbol.encode():
                    raise ValueError("Kafka key / candidate mismatch")
                ingest_message(repo.connection, candidate)
                consumer.commit(message=message, asynchronous=False)
                processed += 1
                LOG.info("STAGED %s %s", candidate.instrument_type, candidate.symbol)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
