"""Running totals per tenant and UTC day, exactly once, with Kafka transactions.

The service reads usage.events (read_committed) and adds each record to its tenant's total for
that day. Then one Kafka transaction writes the changed totals to the compacted topic
usage.totals (to the same partition number as the input record), parks rejects on the
dead-letter topic, and commits the input offsets. Either all of it happens or none of it does.
Offsets go in with the consumer-group metadata (KIP-447), so a stale instance that wakes up
after a rebalance is fenced by the broker (ADR-0001).

State lives in Kafka, not in this process. When a partition is assigned, the service rebuilds:
- the totals, by reading usage.totals for that partition to its end (last value per key wins);
- the duplicate filter, by re-reading the DEDUPE_WINDOW input records before the committed
  offset. Those are exactly the records the filter held when that offset was committed.

A retry of an event_id has the same tenant key, so it lands on the same partition and is
dropped if it arrives within DEDUPE_WINDOW records of the original. Older retries are not
caught here (ADR-0002).

Crash-only: any Kafka error aborts the open transaction and exits. The restart rebuilds state
from Kafka, so there is no in-process recovery path to get wrong.
"""

import json
import logging
import signal
import socket
import threading
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC
from itertools import batched
from types import FrameType
from uuid import UUID

from confluent_kafka import (
    OFFSET_BEGINNING,
    Consumer,
    KafkaError,
    KafkaException,
    Message,
    Producer,
    TopicPartition,
)

from eventplat.config import Settings
from eventplat.deadletter import decode
from eventplat.events import UsageEvent

log = logging.getLogger("eventplat.totals")

BATCH_SIZE = 500
CONSUMER_GROUP = "totals"
DEDUPE_WINDOW = 50_000  # input records per partition
TX_TIMEOUT_S = 30.0
# librdkafka pre-fetches up to 64 MiB per consumer by default; a few MiB keeps up just as well.
PREFETCH_KIB = 8_192


@dataclass
class Total:
    tenant_id: str
    day: str
    input_tokens: int = 0
    output_tokens: int = 0
    events: int = 0

    @property
    def key(self) -> str:
        return f"{self.tenant_id}|{self.day}"

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Total":
        return cls(**json.loads(raw))


class DedupeWindow:
    """The event_ids among the last `size` input offsets of one partition.

    Every usable record is recorded, duplicate or not, so the window depends only on which
    offsets it covers. That makes a window rebuilt by replaying those offsets identical to
    the live one.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._order: deque[tuple[int, UUID]] = deque()
        self._last_offset: dict[UUID, int] = {}

    def __len__(self) -> int:
        return len(self._last_offset)

    def seen(self, event_id: UUID) -> bool:
        return event_id in self._last_offset

    def record(self, offset: int, event_id: UUID) -> None:
        self._order.append((offset, event_id))
        self._last_offset[event_id] = offset
        while self._order and self._order[0][0] <= offset - self._size:
            old_offset, old_id = self._order.popleft()
            if self._last_offset.get(old_id) == old_offset:
                del self._last_offset[old_id]


@dataclass
class PartitionState:
    window: DedupeWindow
    totals: dict[str, Total] = field(default_factory=dict[str, Total])
    duplicates: int = 0

    def apply(self, offset: int, event: UsageEvent) -> Total | None:
        """Add the event to its total. Returns the changed total, or None for a duplicate."""
        duplicate = self.window.seen(event.event_id)
        self.window.record(offset, event.event_id)
        if duplicate:
            self.duplicates += 1
            return None
        day = event.occurred_at.astimezone(UTC).date().isoformat()
        key = f"{event.tenant_id}|{day}"
        total = self.totals.get(key) or Total(event.tenant_id, day)
        total.input_tokens += event.input_tokens
        total.output_tokens += event.output_tokens
        total.events += 1
        self.totals[key] = total
        return total


def read_partition(
    bootstrap: str, topic: str, partition: int, start: int, stop: int | None = None
) -> Iterator[Message]:
    """Committed records of one partition, from `start` up to `stop` (exclusive) or the end."""
    if stop is not None and stop <= start:
        return
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"restore-{uuid.uuid4().hex}",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
            "auto.offset.reset": "earliest",  # start below the retention point: begin there
            "isolation.level": "read_committed",
            "queued.max.messages.kbytes": PREFETCH_KIB,
        }
    )
    try:
        consumer.assign([TopicPartition(topic, partition, start)])
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            err = msg.error()
            if err is not None:
                if err.code() == KafkaError._PARTITION_EOF:
                    return
                raise KafkaException(err)
            if stop is not None and (msg.offset() or 0) >= stop:
                return
            yield msg
    finally:
        consumer.close()


def read_totals(bootstrap: str, topic: str) -> dict[str, Total]:
    """The latest committed total for every key, across all partitions."""
    probe = Consumer({"bootstrap.servers": bootstrap, "group.id": f"probe-{uuid.uuid4().hex}"})
    try:
        partitions = list(probe.list_topics(topic, timeout=TX_TIMEOUT_S).topics[topic].partitions)
    finally:
        probe.close()
    totals: dict[str, Total] = {}
    for partition in partitions:
        for msg in read_partition(bootstrap, topic, partition, OFFSET_BEGINNING):
            total = Total.from_bytes(msg.value() or b"{}")
            totals[total.key] = total
    return totals


class TotalsService:
    def __init__(self, settings: Settings, instance_id: str) -> None:
        self._settings = settings
        self._consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap,
                "group.id": CONSUMER_GROUP,
                # Static membership: a restarted instance rejoins as itself and gets its
                # partitions back at once, instead of waiting out the dead member's session.
                "group.instance.id": instance_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "isolation.level": "read_committed",
                "queued.max.messages.kbytes": PREFETCH_KIB,
            }
        )
        # A stable id per instance: on restart, init_transactions aborts whatever the previous
        # life of this instance left open.
        self._producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap,
                "transactional.id": f"totals-{instance_id}",
                "transaction.timeout.ms": 10_000,
            }
        )
        self._producer.init_transactions(TX_TIMEOUT_S)
        self._states: dict[int, PartitionState] = {}

    def restore(self, partition: int) -> PartitionState:
        s = self._settings
        [tp] = self._consumer.committed(
            [TopicPartition(s.usage_topic, partition)], timeout=TX_TIMEOUT_S
        )
        committed = max(tp.offset, 0)
        state = PartitionState(DedupeWindow(DEDUPE_WINDOW))
        for msg in read_partition(s.kafka_bootstrap, s.totals_topic, partition, OFFSET_BEGINNING):
            total = Total.from_bytes(msg.value() or b"{}")
            state.totals[total.key] = total
        start = max(0, committed - DEDUPE_WINDOW)
        replay = read_partition(s.kafka_bootstrap, s.usage_topic, partition, start, committed)
        for chunk in batched(replay, BATCH_SIZE):  # streamed: never the whole window in memory
            good, _ = decode(list(chunk))
            for msg, event in good:
                state.window.record(msg.offset() or 0, event.event_id)
        log.info(
            "partition %d restored at offset %d: %d totals, %d ids in window",
            partition,
            committed,
            len(state.totals),
            len(state.window),
        )
        return state

    def _on_assign(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        for tp in partitions:
            self._states[tp.partition] = self.restore(tp.partition)

    def _on_revoke(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        for tp in partitions:
            self._states.pop(tp.partition, None)

    def process(self, messages: list[Message]) -> None:
        good, rejected = decode(messages)
        changed: dict[tuple[int, str], Total] = {}
        self._producer.begin_transaction()
        try:
            for r in rejected:
                self._producer.produce(
                    self._settings.dead_letter_topic,
                    key=r.message.key(),
                    value=r.message.value(),
                    headers=r.headers,
                )
            for msg, event in good:
                partition = msg.partition() or 0
                total = self._states[partition].apply(msg.offset() or 0, event)
                if total is not None:
                    changed[(partition, total.key)] = total
            for (partition, key), total in changed.items():
                self._producer.produce(
                    self._settings.totals_topic,
                    partition=partition,
                    key=key.encode(),
                    value=total.to_bytes(),
                )
            self._producer.send_offsets_to_transaction(
                self._consumer.position(self._consumer.assignment()),
                self._consumer.consumer_group_metadata(),
                TX_TIMEOUT_S,
            )
            self._producer.commit_transaction(TX_TIMEOUT_S)
        except BaseException:
            try:
                self._producer.abort_transaction(TX_TIMEOUT_S)
            finally:
                raise

    def run(self, stop: threading.Event) -> None:
        self._consumer.subscribe(
            [self._settings.usage_topic],
            on_assign=self._on_assign,
            on_revoke=self._on_revoke,
            on_lost=self._on_revoke,
        )
        log.info("totals consuming %s", self._settings.usage_topic)
        try:
            while not stop.is_set():
                messages = self._consumer.consume(num_messages=BATCH_SIZE, timeout=1.0)
                if messages:
                    self.process(messages)
        finally:
            self._consumer.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    TotalsService(Settings.from_env(), socket.gethostname()).run(stop)


if __name__ == "__main__":
    main()
