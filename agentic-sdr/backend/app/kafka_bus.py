"""Kafka wiring (confluent-kafka / librdkafka).

Producer: idempotent + acks=all — the broker deduplicates producer retries, so
a network blip can't double-write a command. Consumers: auto-commit OFF; the
worker loop commits an offset only after the handler's database transaction has
committed. Combined with the processed_events table this yields at-least-once
delivery with exactly-once *effects*.
"""
from typing import Iterable, Optional

from confluent_kafka import Consumer, Producer

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(component="kafka")

_producer: Optional[Producer] = None


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 5,
            "compression.type": "lz4",
            "client.id": f"{settings.SERVICE_NAME}-producer",
        })
    return _producer


def _delivery_cb(err, msg):
    if err is not None:
        log.error("kafka_delivery_failed", topic=msg.topic(), error=str(err))


def produce(topic: str, key: str, value: bytes, headers: Optional[dict] = None) -> None:
    p = get_producer()
    hdrs = [(k, str(v).encode()) for k, v in (headers or {}).items()]
    p.produce(topic, key=key.encode(), value=value, headers=hdrs, callback=_delivery_cb)
    p.poll(0)


def flush(timeout: float = 10.0) -> int:
    return get_producer().flush(timeout)


def make_consumer(group_id: str, topics: Iterable[str],
                  read_committed: bool = False) -> Consumer:
    config = {
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "group.id": group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        # stage handlers call Claude/Gmail; allow slow processing without a rebalance
        "max.poll.interval.ms": 600_000,
        "session.timeout.ms": 45_000,
        "partition.assignment.strategy": "cooperative-sticky",
        "client.id": f"{settings.SERVICE_NAME}-{group_id}",
    }
    if read_committed:
        # hot-path consumers must not see messages from aborted EOS transactions
        config["isolation.level"] = "read_committed"
    consumer = Consumer(config)
    consumer.subscribe(list(topics))
    return consumer


def make_transactional_producer(transactional_id: str) -> Producer:
    """EOS producer for zone-1 workers: consume→transform→produce is committed
    atomically with the consumer offsets. transactional.id must be stable per
    worker identity so a restarted worker fences its own zombie predecessor."""
    producer = Producer({
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "transactional.id": transactional_id,
        "enable.idempotence": True,
        "acks": "all",
        "linger.ms": 5,
        "compression.type": "lz4",
        "client.id": f"{settings.SERVICE_NAME}-{transactional_id}",
    })
    producer.init_transactions()
    return producer
