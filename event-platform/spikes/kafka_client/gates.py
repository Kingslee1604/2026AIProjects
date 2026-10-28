"""Conformance gates for ADR-0001: confluent-kafka vs aiokafka, against Kafka 4.3.

    uv run --frozen --all-groups python spikes/kafka_client/gates.py confluent|aiokafka

Needs the dev stack (`make up`). G2 kills the broker container with SIGKILL and starts it
again. Raw results go to bench/results/<date>-<host>-kafka-client/<client>.json.

Setup and verification (creating topics, reading topics back) always use one reference
reader, so only the action under test differs between the two candidates.
"""

import asyncio
import json
import pathlib
import platform
import resource
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import date
from importlib.metadata import version
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

BOOTSTRAP = "localhost:29092"
BROKER_CONTAINER = "eventplat-kafka-1-1"
RESTART_RECORDS = 50_000
RESTART_RATE = 2_000  # records/s, so the kill lands mid-stream
SPEED_RECORDS = 200_000
TX_RECORDS = 100
PAYLOAD = b"x" * 200

# ---------------------------------------------------------------- shared setup and checks


def new_topic(partitions: int) -> str:
    name = f"spike.{uuid.uuid4().hex[:10]}"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    admin.create_topics([NewTopic(name, partitions, 1)])[name].result(30)
    return name


def read_all(topic: str, committed_only: bool = True) -> list[bytes]:
    """Reference reader: every record value in the topic, partition by partition."""
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"reader-{uuid.uuid4().hex}",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
            "isolation.level": "read_committed" if committed_only else "read_uncommitted",
        }
    )
    partitions = list(consumer.list_topics(topic, timeout=10).topics[topic].partitions)
    consumer.assign([TopicPartition(topic, p, 0) for p in partitions])
    values: list[bytes] = []
    finished: set[int] = set()
    deadline = time.monotonic() + 120
    while len(finished) < len(partitions) and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        err = msg.error()
        if err is not None:
            if err.code() == KafkaError._PARTITION_EOF:
                finished.add(msg.partition() or 0)
                continue
            raise KafkaException(err)
        values.append(msg.value() or b"")
    consumer.close()
    return values


def seed(topic: str, count: int) -> None:
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
    for i in range(count):
        producer.produce(topic, value=str(i).encode())
    assert producer.flush(30) == 0


def wait_for_broker() -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            AdminClient({"bootstrap.servers": BOOTSTRAP}).list_topics(timeout=5)
            return
        except KafkaException:
            time.sleep(1)
    raise RuntimeError("broker did not come back")


class BrokerKiller:
    """SIGKILLs the broker when triggered, then starts it again."""

    def __init__(self) -> None:
        self.trigger = threading.Event()
        self.down_s = 0.0
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def _run(self) -> None:
        self.trigger.wait()
        started = time.monotonic()
        subprocess.run(
            ["docker", "kill", "-s", "KILL", BROKER_CONTAINER], check=True, capture_output=True
        )
        subprocess.run(["docker", "start", BROKER_CONTAINER], check=True, capture_output=True)
        wait_for_broker()
        self.down_s = round(time.monotonic() - started, 1)

    def join(self) -> None:
        self.trigger.set()  # no-op if already fired
        self._thread.join()


def restart_verdict(topic: str, acked: list[int], failed: int, down_s: float) -> dict[str, Any]:
    counts = Counter(int(v) for v in read_all(topic))
    duplicates = sum(c - 1 for c in counts.values() if c > 1)
    lost = sum(1 for i in acked if counts[i] == 0)
    return {
        "passed": duplicates == 0 and lost == 0,
        "sent": RESTART_RECORDS,
        "acked": len(acked),
        "reported_failed": failed,
        "acked_but_missing": lost,
        "duplicates": duplicates,
        "broker_down_s": down_s,
    }


def tx_verdict(out_topic: str, committed_offset: int) -> dict[str, Any]:
    visible = read_all(out_topic, committed_only=True)
    raw = read_all(out_topic, committed_only=False)
    return {
        "passed": len(visible) == TX_RECORDS
        and all(v.startswith(b"committed:") for v in visible)
        and committed_offset == TX_RECORDS,
        "visible_read_committed": len(visible),
        "aborted_visible": sum(1 for v in visible if v.startswith(b"aborted:")),
        "physically_written": len(raw),
        "input_offset_committed_in_tx": committed_offset,
    }


# ---------------------------------------------------------------- confluent-kafka


def confluent_gates() -> dict[str, Any]:
    results: dict[str, Any] = {"client": "confluent-kafka", "version": version("confluent-kafka")}

    # G1: round trip on Kafka 4.3
    topic = new_topic(1)
    started = time.perf_counter()
    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
    producer.produce(topic, value=b"hello")
    producer.flush(30)
    results["g1_kafka4_round_trip"] = {
        "passed": read_all(topic) == [b"hello"],
        "seconds": round(time.perf_counter() - started, 2),
    }

    # G2: idempotent producer while the broker is killed
    topic = new_topic(3)
    producer = Producer(
        {"bootstrap.servers": BOOTSTRAP, "enable.idempotence": True, "acks": "all", "linger.ms": 5}
    )
    acked: list[int] = []
    failed = 0

    def on_delivery(err: KafkaError | None, msg: Any) -> None:
        nonlocal failed
        if err is None:
            acked.append(int(msg.value()))
        else:
            failed += 1

    killer = BrokerKiller()
    started = time.monotonic()
    for i in range(RESTART_RECORDS):
        while True:
            try:
                producer.produce(
                    topic, key=str(i % 30).encode(), value=str(i).encode(), on_delivery=on_delivery
                )
                break
            except BufferError:
                producer.poll(0.1)
        producer.poll(0)
        if i == RESTART_RECORDS // 3:
            killer.trigger.set()
        time.sleep(max(0.0, started + i / RESTART_RATE - time.monotonic()))
    producer.flush(300)
    killer.join()
    results["g2_no_duplicates_across_broker_kill"] = restart_verdict(
        topic, acked, failed, killer.down_s
    )

    # G3: transactions (abort invisible; commit carries output and input offsets together)
    inp, out = new_topic(1), new_topic(1)
    seed(inp, TX_RECORDS)
    group = f"tx-{uuid.uuid4().hex}"
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "isolation.level": "read_committed",
        }
    )
    consumer.subscribe([inp])
    batch: list[Any] = []
    deadline = time.monotonic() + 60
    while len(batch) < TX_RECORDS and time.monotonic() < deadline:
        batch += [m for m in consumer.consume(TX_RECORDS, 1.0) if m.error() is None]
    tx = Producer({"bootstrap.servers": BOOTSTRAP, "transactional.id": f"tx-{uuid.uuid4().hex}"})
    tx.init_transactions(30)
    tx.begin_transaction()
    for m in batch:
        tx.produce(out, value=b"aborted:" + m.value())
    tx.flush(30)
    tx.abort_transaction(30)
    tx.begin_transaction()
    for m in batch:
        tx.produce(out, value=b"committed:" + m.value())
    tx.send_offsets_to_transaction(
        consumer.position(consumer.assignment()), consumer.consumer_group_metadata(), 30
    )
    tx.commit_transaction(30)
    [committed] = consumer.committed([TopicPartition(inp, 0)], timeout=10)
    consumer.close()
    results["g3_transactions"] = tx_verdict(out, committed.offset) | {
        "fencing": "group metadata (KIP-447)"
    }

    # G4: speed
    topic = new_topic(6)
    producer = Producer(
        {"bootstrap.servers": BOOTSTRAP, "enable.idempotence": True, "acks": "all", "linger.ms": 5}
    )
    started = time.perf_counter()
    for _ in range(SPEED_RECORDS):
        while True:
            try:
                producer.produce(topic, value=PAYLOAD)
                break
            except BufferError:
                producer.poll(0.05)
        producer.poll(0)
    producer.flush(120)
    produce_s = time.perf_counter() - started

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"speed-{uuid.uuid4().hex}",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([topic])
    seen = 0
    started = None
    while seen < SPEED_RECORDS:
        msgs = [m for m in consumer.consume(1000, 1.0) if m.error() is None]
        if msgs and started is None:
            started = time.perf_counter()
        seen += len(msgs)
    consume_s = time.perf_counter() - (started or time.perf_counter())
    consumer.close()
    results["g4_speed"] = speed(produce_s, consume_s)
    return results


# ---------------------------------------------------------------- aiokafka


async def aiokafka_gates() -> dict[str, Any]:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
    from aiokafka.structs import TopicPartition as AioTP

    results: dict[str, Any] = {"client": "aiokafka", "version": version("aiokafka")}

    # G1
    topic = new_topic(1)
    started = time.perf_counter()
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all")
    try:
        await producer.start()
        await producer.send_and_wait(topic, b"hello")
        await producer.stop()
        ok = read_all(topic) == [b"hello"]
        error = None
    except Exception as exc:  # the gate records why it failed
        ok, error = False, repr(exc)
    results["g1_kafka4_round_trip"] = {
        "passed": ok,
        "seconds": round(time.perf_counter() - started, 2),
    } | ({"error": error} if error else {})
    if not ok:
        return results

    # G2
    topic = new_topic(3)
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP, enable_idempotence=True, acks="all", linger_ms=5
    )
    await producer.start()
    killer = BrokerKiller()
    pending: list[tuple[int, asyncio.Future[Any]]] = []
    failed = 0
    started = time.monotonic()
    for i in range(RESTART_RECORDS):
        try:
            fut = await producer.send(topic, key=str(i % 30).encode(), value=str(i).encode())
            pending.append((i, fut))
        except Exception:  # send itself can fail while the broker is down
            failed += 1
        if i == RESTART_RECORDS // 3:
            killer.trigger.set()
        await asyncio.sleep(max(0.0, started + i / RESTART_RATE - time.monotonic()))
    outcomes = await asyncio.gather(*(f for _, f in pending), return_exceptions=True)
    acked = [
        i for (i, _), o in zip(pending, outcomes, strict=True) if not isinstance(o, BaseException)
    ]
    failed += sum(1 for o in outcomes if isinstance(o, BaseException))
    await producer.stop()
    await asyncio.to_thread(killer.join)
    results["g2_no_duplicates_across_broker_kill"] = restart_verdict(
        topic, acked, failed, killer.down_s
    )

    # G3
    inp, out = new_topic(1), new_topic(1)
    seed(inp, TX_RECORDS)
    group = f"tx-{uuid.uuid4().hex}"
    consumer = AIOKafkaConsumer(
        inp,
        bootstrap_servers=BOOTSTRAP,
        group_id=group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        isolation_level="read_committed",
    )
    await consumer.start()
    batch: list[Any] = []
    deadline = time.monotonic() + 60
    while len(batch) < TX_RECORDS and time.monotonic() < deadline:
        for records in (await consumer.getmany(timeout_ms=1000)).values():
            batch += records
    tx = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, transactional_id=f"tx-{uuid.uuid4().hex}")
    await tx.start()
    await tx.begin_transaction()
    for m in batch:
        await tx.send(out, b"aborted:" + m.value)
    await tx.abort_transaction()
    async with tx.transaction():
        for m in batch:
            await tx.send(out, b"committed:" + m.value)
        await tx.send_offsets_to_transaction({AioTP(inp, 0): batch[-1].offset + 1}, group)
    await tx.stop()
    committed = await consumer.committed(AioTP(inp, 0))
    await consumer.stop()
    results["g3_transactions"] = tx_verdict(out, committed or 0) | {
        "fencing": "group id only (pre KIP-447)"
    }

    # G4
    topic = new_topic(6)
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP, enable_idempotence=True, acks="all", linger_ms=5
    )
    await producer.start()
    started = time.perf_counter()
    futures: list[asyncio.Future[Any]] = []
    for _ in range(SPEED_RECORDS):
        futures.append(await producer.send(topic, PAYLOAD))
        if len(futures) >= 10_000:
            await asyncio.gather(*futures)
            futures.clear()
    await asyncio.gather(*futures)
    produce_s = time.perf_counter() - started
    await producer.stop()

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"speed-{uuid.uuid4().hex}",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    seen = 0
    first: float | None = None
    while seen < SPEED_RECORDS:
        batches = await consumer.getmany(timeout_ms=1000, max_records=1000)
        n = sum(len(r) for r in batches.values())
        if n and first is None:
            first = time.perf_counter()
        seen += n
    consume_s = time.perf_counter() - (first or time.perf_counter())
    await consumer.stop()
    results["g4_speed"] = speed(produce_s, consume_s)
    return results


# ---------------------------------------------------------------- report


def speed(produce_s: float, consume_s: float) -> dict[str, Any]:
    return {
        "records": SPEED_RECORDS,
        "record_bytes": len(PAYLOAD),
        "produce_per_s": round(SPEED_RECORDS / produce_s),
        "consume_per_s": round(SPEED_RECORDS / consume_s) if consume_s > 0 else None,
    }


def main() -> None:
    client = sys.argv[1]
    wait_for_broker()
    results = confluent_gates() if client == "confluent" else asyncio.run(aiokafka_gates())
    results["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    results["machine"] = {
        "host": socket.gethostname(),
        "cpu": next(
            (
                line.split(":", 1)[1].strip()
                for line in open("/proc/cpuinfo")
                if line.startswith("model name")
            ),
            platform.processor(),
        ),
        "python": platform.python_version(),
        "kafka_image": "apache/kafka:4.3.1 (1 node)",
    }
    root = pathlib.Path(__file__).resolve().parents[2]
    out = root / "bench" / "results" / f"{date.today()}-{socket.gethostname()}-kafka-client"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{client}.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
