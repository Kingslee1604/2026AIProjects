import pytest
from confluent_kafka import TopicPartition

from eventplat.progress import StallGuard, StuckConsumer


class FakeConsumer:
    def __init__(self, positions: dict[int, int], ends: dict[int, int]) -> None:
        self.positions = positions
        self.ends = ends

    def assignment(self) -> list[TopicPartition]:
        return [TopicPartition("usage.events", p) for p in self.positions]

    def position(self, partitions: list[TopicPartition]) -> list[TopicPartition]:
        return [
            TopicPartition(tp.topic, tp.partition, self.positions[tp.partition])
            for tp in partitions
        ]

    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float = 0.0, cached: bool = False
    ) -> tuple[int, int]:
        return 0, self.ends[partition.partition]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_raises_when_records_wait_and_nothing_is_consumed() -> None:
    clock = Clock()
    guard = StallGuard(FakeConsumer({0: 100, 1: 50}, {0: 160, 1: 50}), 60, clock)
    clock.now = 59
    guard.check(0)  # not yet
    clock.now = 61
    with pytest.raises(StuckConsumer, match="60 records waiting"):
        guard.check(0)


def test_progress_resets_the_clock() -> None:
    clock = Clock()
    guard = StallGuard(FakeConsumer({0: 100}, {0: 160}), 60, clock)
    clock.now = 50
    guard.check(10)
    clock.now = 100
    guard.check(0)  # 50 s since the last progress


def test_idle_with_nothing_to_read_is_fine() -> None:
    clock = Clock()
    guard = StallGuard(FakeConsumer({0: 160}, {0: 160}), 60, clock)
    clock.now = 600
    guard.check(0)


def test_never_fetched_partition_counts_as_waiting() -> None:
    clock = Clock()
    # The offset-lookup fault: assigned, but no position was ever set (offset -1001).
    guard = StallGuard(FakeConsumer({0: -1001}, {0: 40}), 60, clock)
    clock.now = 61
    with pytest.raises(StuckConsumer, match="40 records waiting"):
        guard.check(0)
