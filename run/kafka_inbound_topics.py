"""Create local inbound topics once."""
import os
from confluent_kafka.admin import AdminClient, NewTopic
from ingestion.kafka_inbound import TOPICS


def main():
    admin = AdminClient({"bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "localhost:9092")})
    existing = set(admin.list_topics(timeout=10).topics)
    missing = [x for x in TOPICS.values() if x not in existing]
    if not missing:
        print("Inbound topics already exist")
        return
    for topic, result in admin.create_topics(
        [NewTopic(x, num_partitions=1, replication_factor=1) for x in missing]
    ).items():
        result.result(timeout=30)
        print("CREATED", topic)


if __name__ == "__main__":
    main()
