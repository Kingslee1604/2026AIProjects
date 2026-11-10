"""Poison records written straight to Kafka (bypassing the gateway) end up on the dead-letter
topic with a reason, and the good records around them still reach Postgres.
"""

import time
import uuid

import pytest
from confluent_kafka import Consumer, Producer, TopicPartition

from eventplat.config import Settings
from eventplat.db import Database
from eventplat.events import UsageEvent

pytestmark = pytest.mark.e2e

POISON = [b"not json", b'{"event_id": "missing fields"}', b'{"input_tokens": -5}']


def test_poison_records_are_parked_not_dropped() -> None:
    settings = Settings.from_env()
    run = uuid.uuid4().hex
    good = [
        UsageEvent.model_validate(
            {
                "event_id": str(uuid.uuid4()),
                "tenant_id": f"dlq-{run}",
                "model": "claude-haiku-5-5",
                "input_tokens": 1,
                "output_tokens": 1,
                "occurred_at": "2026-10-27T10:00:00Z",
            }
        )
        for _ in range(3)
    ]

    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap, "acks": "all"})
    for i, event in enumerate(good):
        producer.produce(settings.usage_topic, key=event.key, value=event.to_bytes())
        producer.produce(settings.usage_topic, key=f"poison-{run}".encode(), value=POISON[i])
    assert producer.flush(30) == 0

    parked = read_dead_letters(settings, key=f"poison-{run}".encode(), want=len(POISON))
    assert sorted(value for value, _ in parked) == sorted(POISON)
    assert all(
        headers["error"] and headers["source"].startswith("usage.events/") for _, headers in parked
    )

    with Database(settings) as db:
        deadline = time.monotonic() + 30
        landed = 0
        while landed < len(good) and time.monotonic() < deadline:
            time.sleep(0.5)
            [[landed]] = db.query(
                "SELECT count(*) FROM usage_events WHERE tenant_id = :t", t=f"dlq-{run}"
            )
    assert landed == len(good)


def read_dead_letters(
    settings: Settings, key: bytes, want: int
) -> list[tuple[bytes, dict[str, str]]]:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": f"e2e-{uuid.uuid4().hex}",
            "enable.auto.commit": False,
        }
    )
    metadata = consumer.list_topics(settings.dead_letter_topic, timeout=10)
    partitions = metadata.topics[settings.dead_letter_topic].partitions
    consumer.assign([TopicPartition(settings.dead_letter_topic, p, 0) for p in partitions])
    found: list[tuple[bytes, dict[str, str]]] = []
    deadline = time.monotonic() + 30
    try:
        while len(found) < want and time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None or msg.error() is not None or msg.key() != key:
                continue
            headers = {
                k: v.decode() if isinstance(v, bytes) else (v or "") for k, v in msg.headers() or []
            }
            found.append((msg.value() or b"", headers))
    finally:
        consumer.close()
    return found
