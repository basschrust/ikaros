"""Collect daily FX/GPW observations and optionally publish to Kafka.

Thin compatibility entry point; provider selection and normalization live in
ikaros_app. The output event contract and Kafka topics are unchanged.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from ingestion.kafka_inbound import Observation, TOPICS
from ikaros_app.collection import DailyCollector

LOG = logging.getLogger(__name__)


def observations(*, dataset: str, symbol: str, days: int) -> list[Observation]:
    return DailyCollector().collect(dataset=dataset, symbol=symbol, days=days)


def publish(producer, dataset: str, events: list[Observation]) -> int:
    failures: list[str] = []
    acknowledgements = []

    def delivered(err, message):
        if err:
            failures.append(str(err))
        else:
            acknowledgements.append(message.offset())

    for event in events:
        producer.produce(
            TOPICS[dataset],
            key=event.key.encode("utf8"),
            value=event.serialize(),
            on_delivery=delivered,
        )
        producer.poll(0)
    remaining = producer.flush(60)
    if remaining or failures or len(acknowledgements) != len(events):
        raise RuntimeError(
            f"Unconfirmed Kafka publication: failures={failures[:3]}, "
            f"outstanding={remaining}, acked={len(acknowledgements)}/{len(events)}"
        )
    return len(acknowledgements)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(TOPICS), default="FX")
    parser.add_argument("--symbols", nargs="+", default=["EURPLN=X"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args(argv)
    if args.days < 1 or args.interval < 60:
        parser.error("--days must be positive; --interval must be at least 60")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    producer = None
    if args.write:
        from confluent_kafka import Producer

        producer = Producer(
            {
                "bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "127.0.0.1:9092"),
                "enable.idempotence": True,
                "acks": "all",
                "client.id": "ikaros-inbound-collector",
            }
        )
    collector = DailyCollector()
    while True:
        failed = []
        for symbol in dict.fromkeys(args.symbols):
            try:
                events = collector.collect(dataset=args.dataset, symbol=symbol, days=args.days)
                count = publish(producer, args.dataset, events) if producer else len(events)
                LOG.info(
                    "%s %s fetched=%d %s=%d",
                    args.dataset,
                    symbol,
                    len(events),
                    "published" if producer else "dry_run",
                    count,
                )
            except Exception:
                LOG.exception("COLLECT FAILED %s", symbol)
                failed.append(symbol)
                if not args.continue_on_error:
                    raise
        if failed:
            LOG.error("Failed symbols: %s", ", ".join(failed))
        if not args.loop:
            if failed:
                raise SystemExit(1)
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
