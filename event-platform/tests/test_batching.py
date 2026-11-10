from confluent_kafka import Message

from eventplat.batching import next_batch


class FakeConsumer:
    """Hands out queued records; records how it was asked."""

    def __init__(self, queued: int) -> None:
        self.queued = queued
        self.calls: list[tuple[int, float]] = []

    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[Message]:
        self.calls.append((num_messages, timeout))
        taken = min(num_messages, self.queued)
        self.queued -= taken
        return [Message() for _ in range(taken)]


def test_waits_for_the_first_record_then_lingers_briefly_for_more() -> None:
    consumer = FakeConsumer(queued=3)
    batch = next_batch(consumer, size=500, wait_s=1.0, linger_s=0.05)
    assert len(batch) == 3
    assert consumer.calls == [(1, 1.0), (499, 0.05)]


def test_a_full_batch_is_capped_at_size() -> None:
    consumer = FakeConsumer(queued=2000)
    assert len(next_batch(consumer, size=500, wait_s=1.0)) == 500
    assert consumer.queued == 1500


def test_nothing_arrived() -> None:
    consumer = FakeConsumer(queued=0)
    assert next_batch(consumer, size=500, wait_s=1.0) == []
    assert consumer.calls == [(1, 1.0)]
