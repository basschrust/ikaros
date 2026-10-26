"""Consume inbound Kafka observations into Snowflake; commit offset after MERGE."""

from __future__ import annotations

import argparse
import logging
import os

from ingestion.kafka_inbound import Observation, TOPICS
from ingestion.kafka_snowflake_sink import upsert_observation

LOG = logging.getLogger(__name__)


def persist_and_commit(consumer, connection, message, *, dataset: str):
    event = Observation.deserialize(message.value())
    if event.dataset != dataset:
        raise ValueError("Message dataset does not match topic")
    if message.key() != event.key.encode("utf-8"):
        raise ValueError("Message key does not match payload")
    upsert_observation(connection, event)
    # Snowflake connector autocommit is enabled by default; this commit follows
    # a successful durable MERGE. Never commit offsets on persistence failures.
    consumer.commit(message=message, asynchronous=False)
    return event


def main():
    from confluent_kafka import Consumer, KafkaException
    from config.config import load_config
    from database.repositories.factory import repository_from_config

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=sorted(TOPICS), default="FX")
    p.add_argument("--group", default="ikaros-snowflake-inbound-v1")
    p.add_argument("--max-messages", type=int, default=0, help="0 = continuous")
    args = p.parse_args()
    if args.max_messages < 0:
        p.error("--max-messages must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config("database.yaml")
    if config.get("database", {}).get("driver", "").lower() != "snowflake":
        p.error("Select Snowflake as database driver")
    client = Consumer(
        {
            "bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "localhost:9092"),
            "group.id": args.group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    client.subscribe([TOPICS[args.dataset]])
    processed = 0
    try:
        with repository_from_config(config) as repo:
            while args.max_messages == 0 or processed < args.max_messages:
                msg = client.poll(2.0)
                if msg is None:
                    continue
                if msg.error():
                    raise KafkaException(msg.error())
                event = persist_and_commit(client, repo.connection, msg, dataset=args.dataset)
                processed += 1
                LOG.info("COMMITTED %s close=%s", event.observation_id, event.close)
    finally:
        client.close()
    LOG.info("Processed %d observations", processed)


if __name__ == "__main__":
    main()
