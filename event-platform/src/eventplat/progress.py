"""Stall guard: a consumer that has records waiting but consumes nothing for too long is stuck.

Two faults found in the crash runs left a consumer polling without ever receiving a record:
an offset lookup that failed once and was never retried, and a fatal error the client
reported as an ordinary one. Each cost five minutes until Kafka gave up on the member. The
guard turns any such state into an exception, and the crash-only services restart at once.
"""

import time
from collections.abc import Callable
from typing import Protocol

from confluent_kafka import TopicPartition

WATERMARK_TIMEOUT_S = 5.0


class StuckConsumer(RuntimeError):
    """Records are waiting on assigned partitions, but nothing was consumed for too long."""


class _Consumer(Protocol):
    def assignment(self) -> list[TopicPartition]: ...
    def position(self, partitions: list[TopicPartition]) -> list[TopicPartition]: ...
    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float = ..., cached: bool = ...
    ) -> tuple[int, int]: ...


class StallGuard:
    def __init__(
        self,
        consumer: _Consumer,
        stuck_after_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._consumer = consumer
        self._stuck_after_s = stuck_after_s
        self._clock = clock
        self._last_progress = clock()

    def check(self, consumed: int) -> None:
        """Call once per poll with the number of records it returned."""
        now = self._clock()
        if consumed or now - self._last_progress < self._stuck_after_s:
            if consumed:
                self._last_progress = now
            return
        waiting = self.records_waiting()
        if waiting:
            raise StuckConsumer(
                f"{waiting} records waiting, nothing consumed for {now - self._last_progress:.0f} s"
            )
        self._last_progress = now  # idle with nothing to read: fine, look again later

    def records_waiting(self) -> int:
        assigned = self._consumer.assignment()
        if not assigned:
            return 0
        waiting = 0
        for tp in self._consumer.position(assigned):
            _, end = self._consumer.get_watermark_offsets(
                TopicPartition(tp.topic, tp.partition), timeout=WATERMARK_TIMEOUT_S
            )
            # A partition with no position yet has never been fetched: everything in it waits.
            waiting += max(0, end - max(tp.offset, 0))
        return waiting
