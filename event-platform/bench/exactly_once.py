"""Exactly-once comparison for ADR-0002: Kafka-transaction totals vs Postgres totals.

    uv run --frozen python bench/exactly_once.py

Needs the dev stack (`make up`). Records are produced straight to usage.events (the gateway is
not under test here), with client retries mixed in: every 10th record re-sends the record 500
places earlier, as a client retrying after a lost response would.

Phase 1 (chaos): send at a steady rate while SIGKILLing the totals service and the ledger in
turn, then check both totals against what was sent, to the token.
Phase 2 (backlog): with both consumers stopped, load a backlog, then start each one alone and
time how long it takes to catch up.

Raw results: bench/results/<date>-<host>-exactly-once/results.json
"""

import json
import pathlib
import socket
import subprocess
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import date
from typing import Any

from confluent_kafka import Consumer, Producer, TopicPartition

from eventplat.config import Settings
from eventplat.db import Database
from eventplat.events import UsageEvent
from eventplat.totals import Total

SETTINGS = Settings.from_env()
CONTAINERS = {"kafka": "eventplat-totals-1", "database": "eventplat-ledger-1"}
DAY = "2026-10-27"
TENANTS = 10
RETRY_EVERY, RETRY_DISTANCE = 10, 500

CHAOS_RECORDS, CHAOS_RATE, KILL_EVERY_S = 60_000, 2_000, 4.0
BACKLOG_RECORDS = 200_000
CONVERGE_TIMEOUT_S = 600

Totals = dict[str, tuple[int, int, int]]


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


class Workload:
    """The records to send (retries included) and the totals they must add up to."""

    def __init__(self, records: int) -> None:
        run = uuid.uuid4().hex[:8]
        self.tenants = [f"bench-{run}-{t}" for t in range(TENANTS)]
        unique = [
            UsageEvent.model_validate(
                {
                    "event_id": str(uuid.uuid4()),
                    "tenant_id": self.tenants[i % TENANTS],
                    "model": "claude-haiku-5-5",
                    "input_tokens": 100 + i % 1000,
                    "output_tokens": i % 50,
                    "occurred_at": f"{DAY}T12:00:00Z",
                }
            )
            for i in range(records)
        ]
        self.sends: list[UsageEvent] = []
        for i, event in enumerate(unique):
            self.sends.append(event)
            if i % RETRY_EVERY == 0 and i >= RETRY_DISTANCE:
                self.sends.append(unique[i - RETRY_DISTANCE])
        sums: dict[str, list[int]] = {}
        for e in unique:
            s = sums.setdefault(f"{e.tenant_id}|{DAY}", [0, 0, 0])
            s[0] += e.input_tokens
            s[1] += e.output_tokens
            s[2] += 1
        self.expected: Totals = {k: (a, b, c) for k, (a, b, c) in sums.items()}
        self.retries = len(self.sends) - len(unique)

    def send(self, rate: float | None = None, during: Callable[[int], None] | None = None) -> None:
        producer = Producer(
            {
                "bootstrap.servers": SETTINGS.kafka_bootstrap,
                "enable.idempotence": True,
                "acks": "all",
            }
        )
        started = time.monotonic()
        for i, event in enumerate(self.sends):
            while True:
                try:
                    producer.produce(SETTINGS.usage_topic, key=event.key, value=event.to_bytes())
                    break
                except BufferError:
                    producer.poll(0.05)
            producer.poll(0)
            if during:
                during(i)
            if rate:
                time.sleep(max(0.0, started + i / rate - time.monotonic()))
        assert producer.flush(120) == 0


class TotalsTail:
    """Follows usage.totals from now on and keeps the latest total for the given tenants."""

    def __init__(self, tenants: list[str]) -> None:
        self._tenants = set(tenants)
        self.latest: dict[str, Total] = {}
        self._stop = threading.Event()
        self._consumer = Consumer(
            {
                "bootstrap.servers": SETTINGS.kafka_bootstrap,
                "group.id": f"tail-{uuid.uuid4().hex}",
                "enable.auto.commit": False,
                "isolation.level": "read_committed",
            }
        )
        topic = SETTINGS.totals_topic
        partitions = self._consumer.list_topics(topic, timeout=10).topics[topic].partitions
        self._consumer.assign(
            [
                TopicPartition(
                    topic, p, self._consumer.get_watermark_offsets(TopicPartition(topic, p))[1]
                )
                for p in partitions
            ]
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self._consumer.poll(0.5)
            if msg is None or msg.error() is not None:
                continue
            total = Total.from_bytes(msg.value() or b"{}")
            if total.tenant_id in self._tenants:
                self.latest[total.key] = total

    def view(self) -> Totals:
        return {
            k: (t.input_tokens, t.output_tokens, t.events) for k, t in dict(self.latest).items()
        }

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self._consumer.close()


def database_view(tenants: list[str]) -> Totals:
    with Database(SETTINGS) as db:
        rows = db.query(
            "SELECT tenant_id, day::text, input_tokens, output_tokens, events "
            "FROM usage_totals WHERE tenant_id = ANY(CAST(:t AS text[]))",
            t=tenants,
        )
    return {f"{t}|{d}": (i, o, e) for t, d, i, o, e in rows}


def wait_until(check: Callable[[], bool], timeout: float = CONVERGE_TIMEOUT_S) -> float | None:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if check():
            return round(time.perf_counter() - started, 1)
        time.sleep(0.5)
    return None


def wait_all(
    checks: dict[str, Callable[[], bool]], timeout: float = CONVERGE_TIMEOUT_S
) -> dict[str, float | None]:
    """Seconds until each check first passes, all timed from the same start."""
    started = time.perf_counter()
    times: dict[str, float | None] = dict.fromkeys(checks)
    while time.perf_counter() - started < timeout and None in times.values():
        for name, check in checks.items():
            if times[name] is None and check():
                times[name] = round(time.perf_counter() - started, 1)
        time.sleep(0.5)
    return times


def verdict(expected: Totals, got: Totals, converge_s: float | None) -> dict[str, Any]:
    wrong = sorted(k for k in expected if got.get(k) != expected[k])
    return {
        "exact": not wrong,
        "converge_s": converge_s,
        "keys_wrong": len(wrong),
        "tokens_expected": sum(v[0] + v[1] for v in expected.values()),
        "tokens_counted": sum(v[0] + v[1] for v in got.values()),
        "events_counted": sum(v[2] for v in got.values()),
    }


class MemoryPeak:
    def __init__(self, container: str) -> None:
        self.peak_mib = 0.0
        self._container = container
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            usage = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", self._container)
            value = usage.split("/")[0].strip()
            mib = float(value.rstrip("GiMKB")) * (1024 if "GiB" in value else 1)
            self.peak_mib = max(self.peak_mib, mib)

    def stop(self) -> float:
        self._stop.set()
        self._thread.join()
        return round(self.peak_mib, 1)


def chaos_phase() -> dict[str, Any]:
    work = Workload(CHAOS_RECORDS)
    tail = TotalsTail(work.tenants)
    kills: Counter[str] = Counter()
    next_kill = [time.monotonic() + KILL_EVERY_S]
    order = list(CONTAINERS.items())

    def maybe_kill(_i: int) -> None:
        if time.monotonic() < next_kill[0]:
            return
        path, container = order[sum(kills.values()) % len(order)]
        docker("kill", "-s", "KILL", container)
        docker("start", container)
        kills[path] += 1
        next_kill[0] = time.monotonic() + KILL_EVERY_S

    work.send(rate=CHAOS_RATE, during=maybe_kill)
    times = wait_all(
        {
            "kafka": lambda: tail.view() == work.expected,
            "database": lambda: database_view(work.tenants) == work.expected,
        }
    )
    kafka_s, db_s = times["kafka"], times["database"]
    result = {
        "records": CHAOS_RECORDS,
        "retries_sent": work.retries,
        "rate_per_s": CHAOS_RATE,
        "sigkills": dict(kills),
        "kafka": verdict(work.expected, tail.view(), kafka_s),
        "database": verdict(work.expected, database_view(work.tenants), db_s),
    }
    tail.close()
    return result


def backlog_phase() -> dict[str, Any]:
    work = Workload(BACKLOG_RECORDS)
    docker("stop", *CONTAINERS.values())
    tail = TotalsTail(work.tenants)
    work.send()
    result: dict[str, Any] = {"records": BACKLOG_RECORDS, "retries_sent": work.retries}

    views: dict[str, Callable[[], Totals]] = {
        "kafka": tail.view,
        "database": lambda: database_view(work.tenants),
    }
    for path, container in CONTAINERS.items():
        docker("start", container)
        memory = MemoryPeak(container)
        seconds = wait_until(lambda v=views[path]: v() == work.expected)
        result[path] = verdict(work.expected, views[path](), seconds) | {
            "records_per_s": round(len(work.sends) / seconds) if seconds else None,
            "peak_mib": memory.stop(),
        }
    tail.close()
    return result


def main() -> None:
    cpu = next(
        line.split(":", 1)[1].strip()
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name")
    )
    results = {
        "date": str(date.today()),
        "machine": {
            "host": socket.gethostname(),
            "cpu": cpu,
            "kafka": "apache/kafka:4.3.1, 1 node",
        },
        "chaos": chaos_phase(),
        "backlog": backlog_phase(),
    }
    out = (
        pathlib.Path(__file__).parent
        / "results"
        / f"{date.today()}-{socket.gethostname()}-exactly-once"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
