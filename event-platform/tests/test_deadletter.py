import uuid

import pytest
from confluent_kafka import KafkaError, KafkaException, Message

from eventplat.deadletter import decode
from eventplat.events import UsageEvent

GOOD = UsageEvent.model_validate(
    {
        "event_id": str(uuid.uuid4()),
        "tenant_id": "acme",
        "model": "claude-sonnet-5-5",
        "input_tokens": 10,
        "output_tokens": 5,
        "occurred_at": "2026-10-27T10:00:00Z",
    }
)


def message(value: bytes | None, offset: int = 0, error: KafkaError | None = None) -> Message:
    return Message(topic="usage.events", partition=2, offset=offset, value=value, error=error)


def test_decode_splits_good_from_rejected() -> None:
    batch = [
        message(GOOD.to_bytes(), 0),
        message(b"not json", 1),
        message(b'{"event_id": "x"}', 2),
        message(None, 3),
    ]
    good, rejected = decode(batch)
    assert [event for _, event in good] == [GOOD]
    assert [r.message.offset() for r in rejected] == [1, 2, 3]
    assert all(r.reason for r in rejected)


def test_decode_skips_non_fatal_client_events() -> None:
    eof = KafkaError(KafkaError._PARTITION_EOF)
    good, rejected = decode([message(None, error=eof), message(GOOD.to_bytes())])
    assert len(good) == 1
    assert rejected == []


def test_decode_raises_on_fatal_error() -> None:
    fatal = KafkaError(KafkaError._FATAL, fatal=True)
    with pytest.raises(KafkaException):
        decode([message(None, error=fatal)])
