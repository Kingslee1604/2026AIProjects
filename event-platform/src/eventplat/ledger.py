"""Ledger writer: copies usage records from Kafka into Postgres, once per event_id.

Order per batch: park unusable records on the dead-letter topic, write good rows to Postgres
(one statement, so all or nothing), then commit the Kafka offsets. A crash before the commit
replays the batch: `ON CONFLICT DO NOTHING` drops rows already written, and a re-parked dead
letter is a harmless duplicate. At-least-once delivery plus an idempotent write gives each
event_id exactly one row, and no record is ever skipped without a trace.

The same statement keeps usage_totals (per tenant and UTC day). This is the database side of
the exactly-once comparison in ADR-0002; the Kafka side is eventplat.totals.
"""

import logging
import signal
import socket
import threading
from dataclasses import dataclass
from types import FrameType

from confluent_kafka import Consumer, Message

from eventplat.config import Settings
from eventplat.db import Database
from eventplat.deadletter import DeadLetters, decode
from eventplat.events import UsageEvent

log = logging.getLogger("eventplat.ledger")

BATCH_SIZE = 500
CONSUMER_GROUP = "ledger"

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS usage_events (
    event_id        uuid PRIMARY KEY,
    tenant_id       text NOT NULL,
    model           text NOT NULL,
    input_tokens    bigint NOT NULL,
    output_tokens   bigint NOT NULL,
    occurred_at     timestamptz NOT NULL,
    kafka_partition int NOT NULL,
    kafka_offset    bigint NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now()
)
""",
    """
CREATE TABLE IF NOT EXISTS usage_totals (
    tenant_id     text NOT NULL,
    day           date NOT NULL,
    input_tokens  bigint NOT NULL,
    output_tokens bigint NOT NULL,
    events        bigint NOT NULL,
    PRIMARY KEY (tenant_id, day)
)
""",
)

# One statement: insert the batch, then add only the rows that were really inserted to the
# per-tenant, per-UTC-day totals. A duplicate event_id is never inserted, so it is never added.
INSERT = """
WITH inserted AS (
    INSERT INTO usage_events (event_id, tenant_id, model, input_tokens, output_tokens,
                              occurred_at, kafka_partition, kafka_offset)
    SELECT * FROM unnest(
        CAST(:event_ids AS uuid[]), CAST(:tenant_ids AS text[]), CAST(:models AS text[]),
        CAST(:input_tokens AS bigint[]), CAST(:output_tokens AS bigint[]),
        CAST(:occurred_ats AS timestamptz[]), CAST(:partitions AS int[]),
        CAST(:offsets AS bigint[])
    )
    ON CONFLICT (event_id) DO NOTHING
    RETURNING tenant_id, occurred_at, input_tokens, output_tokens
)
INSERT INTO usage_totals AS t (tenant_id, day, input_tokens, output_tokens, events)
SELECT tenant_id, (occurred_at AT TIME ZONE 'UTC')::date,
       sum(input_tokens), sum(output_tokens), count(*)
FROM inserted
GROUP BY 1, 2
ON CONFLICT (tenant_id, day) DO UPDATE SET
    input_tokens  = t.input_tokens + EXCLUDED.input_tokens,
    output_tokens = t.output_tokens + EXCLUDED.output_tokens,
    events        = t.events + EXCLUDED.events
"""


@dataclass(frozen=True)
class Row:
    event: UsageEvent
    partition: int
    offset: int

    @classmethod
    def of(cls, msg: Message, event: UsageEvent) -> "Row":
        return cls(event, msg.partition() or 0, msg.offset() or 0)


def write_rows(db: Database, rows: list[Row]) -> None:
    db.execute(
        INSERT,
        event_ids=[str(r.event.event_id) for r in rows],
        tenant_ids=[r.event.tenant_id for r in rows],
        models=[r.event.model for r in rows],
        input_tokens=[r.event.input_tokens for r in rows],
        output_tokens=[r.event.output_tokens for r in rows],
        occurred_ats=[r.event.occurred_at for r in rows],
        partitions=[r.partition for r in rows],
        offsets=[r.offset for r in rows],
    )


def run(settings: Settings, stop: threading.Event) -> None:
    db = Database(settings)
    for statement in SCHEMA:
        db.execute(statement)
    dead_letters = DeadLetters(settings.kafka_bootstrap, settings.dead_letter_topic)
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": CONSUMER_GROUP,
            # Static membership: a restarted ledger rejoins as itself and resumes at once.
            "group.instance.id": socket.gethostname(),
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([settings.usage_topic])
    log.info("ledger consuming %s", settings.usage_topic)
    try:
        while not stop.is_set():
            messages = consumer.consume(num_messages=BATCH_SIZE, timeout=1.0)
            if not messages:
                continue
            good, rejected = decode(messages)
            if rejected:
                dead_letters.park(rejected)
            if good:
                write_rows(db, [Row.of(msg, event) for msg, event in good])
            consumer.commit(asynchronous=False)
    finally:
        consumer.close()
        dead_letters.close()
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(Settings.from_env(), stop)


if __name__ == "__main__":
    main()
