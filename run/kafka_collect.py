"""Poll OpenBB daily market data and publish observations to Kafka (no Snowflake writes)."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import logging
import os
import time

import pandas as pd
from confluent_kafka import Producer

from ingestion.kafka_inbound import Observation, TOPICS
from ingestion.prices import fetch_eod_prices
from run.ingest_fx_rates import fetch_fx_history

LOG = logging.getLogger(__name__)


def observations(*, dataset: str, symbol: str, days: int):
    # Provider end date is exclusive. Include today's potentially partial daily candle.
    today = date.today()
    start = today - timedelta(days=days)
    end = today + timedelta(days=1)
    if dataset == "FX":
        frame = fetch_fx_history(symbol, start.isoformat(), end.isoformat())
    else:
        frame = fetch_eod_prices(symbol=symbol, start_date=start.isoformat(), end_date=end.isoformat())
    if frame.empty:
        return []
    results = []
    for row in frame.to_dict("records"):
        def value(field):
            x = row.get(field)
            return None if x is None or pd.isna(x) else float(x)
        day = pd.Timestamp(row["date"]).date()
        if start <= day <= today:
            results.append(Observation(
                dataset=dataset, symbol=symbol, day=day,
                open=value("open"), high=value("high"), low=value("low"),
                close=value("close"), volume=value("volume"),
                dividend=value("dividend"), split_ratio=value("split_ratio"),
            ))
    return results


def publish(producer, dataset: str, events: list[Observation]):
    failed = []
    successes = []
    def callback(err, message):
        if err:
            failed.append(str(err))
        else:
            successes.append(message.offset())

    for event in events:
        producer.produce(TOPICS[dataset], key=event.key.encode("utf8"),
                         value=event.serialize(), on_delivery=callback)
        producer.poll(0)
    outstanding = producer.flush(60)
    if failed or outstanding or len(successes) != len(events):
        raise RuntimeError(f"Unconfirmed Kafka publication: failures={failed[:3]}, "
                           f"outstanding={outstanding}, acked={len(successes)}/{len(events)}")
    return len(successes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=sorted(TOPICS), default="FX")
    p.add_argument("--symbols", nargs="+", default=["EURPLN=X"])
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=300, help="Seconds between polling rounds")
    p.add_argument("--write", action="store_true", help="Publish to Kafka (default dry-run)")
    p.add_argument("--continue-on-error", action="store_true")
    args = p.parse_args()
    if args.days < 1 or args.interval < 60:
        p.error("--days must be positive; --interval must be at least 60 seconds")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    producer = Producer({
        "bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "localhost:9092"),
        "enable.idempotence": True, "acks": "all", "client.id": "ikaros-inbound-collector",
    }) if args.write else None
    while True:
        failed = []
        for symbol in dict.fromkeys(args.symbols):
            try:
                events = observations(dataset=args.dataset, symbol=symbol, days=args.days)
                n = publish(producer, args.dataset, events) if producer else len(events)
                LOG.info("%s %s fetched=%d %s=%d", args.dataset, symbol, len(events),
                         "published" if args.write else "dry_run", n)
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
