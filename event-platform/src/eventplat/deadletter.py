"""Dead-letter topic: records a consumer cannot use are parked here, never dropped.

Each parked record keeps its original key and bytes, plus headers saying why it was rejected
and where it came from, so it can be inspected, fixed and replayed.
"""

import logging
from dataclasses import dataclass

from confluent_kafka import KafkaError, KafkaException, Message, Producer
from pydantic import ValidationError

from eventplat.events import UsageEvent

log = logging.getLogger("eventplat.deadletter")

FLUSH_TIMEOUT_S = 30.0
MAX_REASON_CHARS = 500


@dataclass(frozen=True)
class Rejected:
    message: Message
    reason: str

    @property
    def source(self) -> str:
        return f"{self.message.topic()}/{self.message.partition()}/{self.message.offset()}"

    @property
    def headers(self) -> dict[str, str | bytes | None]:
        return {"error": self.reason, "source": self.source}


def decode(messages: list[Message]) -> tuple[list[tuple[Message, UsageEvent]], list[Rejected]]:
    """Split a batch into usable records and rejects. Fatal client errors raise."""
    good: list[tuple[Message, UsageEvent]] = []
    rejected: list[Rejected] = []
    for msg in messages:
        err = msg.error()
        if err is not None:
            if err.fatal():
                raise KafkaException(err)
            log.warning("kafka: %s", err)
            continue
        try:
            good.append((msg, UsageEvent.from_bytes(msg.value() or b"")))
        except ValidationError as exc:
            rejected.append(Rejected(msg, str(exc)[:MAX_REASON_CHARS]))
    return good, rejected


class DeadLetters:
    def __init__(self, bootstrap: str, topic: str) -> None:
        self._topic = topic
        self._producer = Producer(
            {"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all"}
        )

    def park(self, rejected: list[Rejected]) -> None:
        """Returns only when every record is stored; raises otherwise, so offsets stay put."""
        failures: list[KafkaError] = []

        def on_delivery(err: KafkaError | None, _msg: Message) -> None:
            if err is not None:
                failures.append(err)

        for r in rejected:
            log.warning("dead-lettering %s: %s", r.source, r.reason.splitlines()[0])
            self._producer.produce(
                self._topic,
                key=r.message.key(),
                value=r.message.value(),
                headers=r.headers,
                on_delivery=on_delivery,
            )
        unsent = self._producer.flush(FLUSH_TIMEOUT_S)
        if unsent or failures:
            raise KafkaException(failures[0] if failures else f"{unsent} dead letters unsent")

    def close(self) -> None:
        self._producer.flush(FLUSH_TIMEOUT_S)
