"""Reading a batch from Kafka without making a quiet topic wait for a full one."""

from typing import Protocol

from confluent_kafka import Message

# How long to keep collecting once the first record of a batch is in. Measured at ~450
# records/s, in CPU per 1000 records for Kafka / ledger / totals (ADR-0005):
#   0 s (only what had already arrived): 1.7-2.6 s / 0.45-0.66 s / 0.6-1.5 s
#   0.05 s:                               1.4-1.7 s / 0.28-0.31 s / 0.38-0.74 s
#   0.2 s:                                0.5-0.7 s / 0.11-0.14 s / 0.12-0.19 s
# Each batch costs a Kafka transaction (totals) and an offset commit (both), so small batches
# are expensive; 0.2 s is as cheap as the old full-second wait.
LINGER_S = 0.2


class _Consumer(Protocol):
    def consume(self, num_messages: int = 1, timeout: float = -1) -> list[Message]: ...


def next_batch(
    consumer: _Consumer, size: int, wait_s: float, linger_s: float = LINGER_S
) -> list[Message]:
    """Up to `size` records: wait up to `wait_s` for the first, then up to `linger_s` more.

    consume(size, timeout) on its own waits until it has `size` records or the timeout ends,
    so at low rates every record waited up to the whole timeout before it was processed: the
    ledger took 0.5 s at the median and 1 s at worst to write a record (bench/load.py).
    """
    first = consumer.consume(num_messages=1, timeout=wait_s)
    if not first or size == 1:
        return first
    return first + consumer.consume(num_messages=size - 1, timeout=linger_s)
