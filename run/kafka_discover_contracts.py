"""Discover GPW derivatives candidates and publish them to Kafka.

No registration to DERIVATIVE_CONTRACTS. An empty/stale source is a failure,
not evidence that active contracts have disappeared.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os

import requests

from ingestion.contract_discovery import parse_bankier_futures, parse_money_options

LOG = logging.getLogger(__name__)
TOPIC = "ikaros.derivatives.discovery.v1"
FUTURES_URL = "https://www.bankier.pl/gielda/notowania/futures"
OPTIONS_URL = "https://www.money.pl/gielda/gpw/opcje/"


def load(url: str) -> str:
    response = requests.get(
        url, timeout=25, headers={"User-Agent": "IkarosResearch/1.0 (+manual-source-review)"}
    )
    response.raise_for_status()
    return response.text


def run(*, publish: bool, snapshot_futures: str | None = None, snapshot_options: str | None = None):
    def read(url, path):
        if path:
            from pathlib import Path

            return Path(path).read_text(encoding="utf-8")
        return load(url)

    stamp = datetime.now(timezone.utc).isoformat()
    futures = parse_bankier_futures(read(FUTURES_URL, snapshot_futures), as_of=stamp)
    options = parse_money_options(read(OPTIONS_URL, snapshot_options), as_of=stamp)
    candidates = futures + options
    if not candidates:
        raise RuntimeError(
            "No derivatives candidates found: source may have changed; refusing empty update"
        )
    LOG.info("Candidates: futures=%d options=%d publish=%s", len(futures), len(options), publish)
    if not publish:
        for c in candidates[:15]:
            LOG.info("CANDIDATE %s %s expiry=%s", c.instrument_type, c.symbol, c.expiry_date)
        return
    from confluent_kafka import Producer

    producer = Producer(
        {
            "bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "127.0.0.1:9092"),
            "enable.idempotence": True,
            "acks": "all",
        }
    )
    failures = []
    ack = []

    def delivered(err, msg):
        if err:
            failures.append(str(err))
        else:
            ack.append(msg.offset())

    for c in candidates:
        producer.produce(TOPIC, key=c.symbol.encode(), value=c.encode(), on_delivery=delivered)
        producer.poll(0)
    remaining = producer.flush(60)
    if remaining or failures or len(ack) != len(candidates):
        raise RuntimeError(f"Kafka delivery failed remaining={remaining}, errors={failures[:2]}")
    LOG.info("Published %d candidates", len(candidates))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true", help="Publish candidates to Kafka")
    p.add_argument("--futures-html", help="Use local snapshot instead of network")
    p.add_argument("--options-html", help="Use local snapshot instead of network")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(publish=args.write, snapshot_futures=args.futures_html, snapshot_options=args.options_html)


if __name__ == "__main__":
    main()
