"""Create derivatives discovery topic on single-node development Kafka."""

import os


def main():
    from confluent_kafka.admin import AdminClient, NewTopic

    topic = "ikaros.derivatives.discovery.v1"
    admin = AdminClient({"bootstrap.servers": os.getenv("IKAROS_KAFKA_BROKERS", "127.0.0.1:9092")})
    if topic in admin.list_topics(timeout=10).topics:
        print("EXISTS", topic)
        return
    admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])[topic].result(
        timeout=30
    )
    print("CREATED", topic)


if __name__ == "__main__":
    main()
