"""Discover derivative candidates from listing adapters and optionally publish to Kafka.

This retains the existing discovery topic and Candidate event schema. It NEVER
promotes stock futures, currency futures or options to verified contracts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os

from providers.derivatives.listings import collect_candidates, sources

LOG = logging.getLogger(__name__)
TOPIC = 'ikaros.derivatives.discovery.v1'


def run(*, publish: bool, snapshot_futures: str | None = None,
        snapshot_options: str | None = None, options_source: str = 'bankier'):
    adapters = sources(futures_html=snapshot_futures, options_html=snapshot_options,
                       options_source=options_source)
    candidates, counts = collect_candidates(adapters, as_of=datetime.now(timezone.utc).isoformat())
    LOG.info('Candidate counts by source: %s', counts)
    LOG.info('Total candidates=%d, publish=%s', len(candidates), publish)
    if not publish:
        for item in candidates[:25]:
            LOG.info('CANDIDATE %s %s expiry=%s source=%s',item.instrument_type,
                     item.symbol,item.expiry_date,item.source)
        return candidates
    from confluent_kafka import Producer
    producer = Producer({'bootstrap.servers':os.getenv('IKAROS_KAFKA_BROKERS','127.0.0.1:9092'),
                         'enable.idempotence':True,'acks':'all'})
    failures: list[str] = []
    acks: list[int] = []
    def delivered(err, message):
        if err: failures.append(str(err))
        else: acks.append(message.offset())
    for item in candidates:
        # Preserve current sink's key validation and topic schema.
        producer.produce(TOPIC,key=item.symbol.encode(),value=item.encode(),on_delivery=delivered)
        producer.poll(0)
    outstanding = producer.flush(60)
    if failures or outstanding or len(acks) != len(candidates):
        raise RuntimeError(f'Kafka delivery incomplete: errors={failures[:3]} outstanding={outstanding} acked={len(acks)}/{len(candidates)}')
    LOG.info('Published %d discovery events',len(candidates))
    return candidates


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='Publish candidates to Kafka; default dry run')
    parser.add_argument('--futures-html',help='Offline futures page')
    parser.add_argument('--options-html',help='Offline options page for selected single source')
    parser.add_argument('--options-source',choices=('bankier','money','both'),default='bankier')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    run(publish=args.write,snapshot_futures=args.futures_html,
        snapshot_options=args.options_html,options_source=args.options_source)


if __name__ == '__main__':
    main()
