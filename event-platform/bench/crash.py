"""Crash harness (step 4): break the stack while it works, then prove that no record was lost,
none was counted twice, and every tenant's order was kept.

    make crash-stack-up
    make bench-crash                         # every scenario
    make bench-crash SCENARIOS="brokers"     # some of them

Runs against the three-node crash stack (compose.crash.yaml). In each scenario, CLIENTS clients
send usage records through the three gateways, RATE records/s in all, for LOAD_S seconds while
something breaks:

- brokers:   SIGKILL one Kafka node, restart it, wait until every partition has all three
             replicas in sync again (timed), then the next node.
- consumers: scale the totals service and the ledger to two instances each (a rebalance),
             SIGKILL the instances in turn, then scale back to one each (another rebalance).
- mixed:     SIGKILL a random part every few seconds: a Kafka node, a gateway, a totals or a
             ledger instance.
- slow-db:   throttle Postgres to 5% of one CPU for 30 s. The gateways write only to Kafka, so
             they should not notice; the ledger falls behind and has to catch up.
- replay:    rebuild a second ledger from offset 0 of usage.events, into its own database, and
             compare it with the live ledger row for row. Runs after the others, with no load.

Each client owns one tenant and sends that tenant's records one at a time, waiting for the 202
before the next, so the tenant's order is defined. A refused or failed send is retried on the
next gateway with the same event_id, and every 10th send re-sends an earlier record, as a
client that lost a response would. Usage only: budgets under gateway kills are covered by
bench/budget_chaos.py.

Checks per scenario, once the load stops:
- lost:  acknowledged records missing from the ledger;
- extra: ledger rows for the scenario's tenants that were never sent;
- exact: the database totals and the Kafka totals (usage.totals) equal what was acknowledged,
  to the token, and how many seconds after the last send they got there;
- order: per tenant, the ledger's Kafka offsets in send order only go up;
- ack latency as the client sees it (retries and failovers included), and stack memory.

Durations use time.perf_counter, never the wall clock (the WSL clock can jump).

Raw results: bench/results/<date>-<host>-crash/results.json (or the folder CRASH_RESULTS names)
"""

import dataclasses
import itertools
import json
import os
import pathlib
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, ClassVar

import httpx2
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient
from exactly_once import Totals, TotalsTail, database_view, verdict, wait_all

from eventplat.config import Settings
from eventplat.db import Database

SETTINGS = Settings.from_env()
ROOT = pathlib.Path(__file__).parent.parent
COMPOSE = ("docker", "compose", "-f", "compose.yaml", "-f", "compose.crash.yaml")
PROJECT = "eventplat-crash"
GATEWAYS = ("http://127.0.0.1:8100", "http://127.0.0.1:8101", "http://127.0.0.1:8102")
BROKERS = ("kafka-1", "kafka-2", "kafka-3")
DAY = str(date.today())

CLIENTS, RATE, LOAD_S = 24, 600, 60.0
RESEND_EVERY, RESEND_DISTANCE = 10, 20
SEND_ATTEMPTS, RETRY_PAUSE_S = 150, 0.2
CONVERGE_TIMEOUT_S = 300
STALL_WINDOW_S = 15.0

BROKER_KILL_EVERY_S, BROKER_DOWN_S, IN_SYNC_TIMEOUT_S = 8.0, 5.0, 120.0
CONSUMER_KILL_EVERY_S, SCALE_DOWN_AT_S = 6.0, 40.0
MIXED_KILL_EVERY_S = 5.0
THROTTLE_AT_S, THROTTLE_FOR_S, THROTTLE_CPUS = 15.0, 30.0, "0.05"
REPLAY_TIMEOUT_S = 600


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, cwd=ROOT).stdout


def compose(*args: str) -> str:
    return run(*COMPOSE, "--profile", "chaos", *args)


def containers(service: str) -> list[str]:
    """Running containers of a service."""
    return compose("ps", "--quiet", service).split()


def sigkill(container: str, down_s: float = 0.0) -> None:
    run("docker", "kill", "--signal", "KILL", container)
    time.sleep(down_s)
    run("docker", "start", container)


def percentiles_ms(seconds: list[float]) -> dict[str, float | None]:
    if len(seconds) < 2:
        return {"p50": None, "p99": None, "max": None}
    q = statistics.quantiles(seconds, n=100)
    return {
        "p50": round(q[49] * 1000, 1),
        "p99": round(q[98] * 1000, 1),
        "max": round(max(seconds) * 1000, 1),
    }


class Client(threading.Thread):
    """One tenant, sent in order. `sent` holds every unique record that was acknowledged."""

    def __init__(self, n: int, run_id: str, stop: threading.Event) -> None:
        super().__init__(daemon=True)
        self.tenant = f"crash-{run_id}-{n}"
        self.home = n
        self.stop = stop
        self.sent: list[dict[str, Any]] = []
        self.unacked: list[dict[str, Any]] = []
        self.acks: list[tuple[float, float]] = []  # (started, seconds to the 202)
        self.resends = 0
        self.failovers = 0

    def post(self, http: httpx2.Client, record: dict[str, Any]) -> bool:
        for _ in range(SEND_ATTEMPTS):
            try:
                status = http.post(f"{GATEWAYS[self.home % len(GATEWAYS)]}/v1/usage", json=record)
                if status.status_code == 202:
                    return True
                if status.status_code != 503:
                    raise RuntimeError(f"gateway answered {status.status_code}: {status.text}")
            except httpx2.TransportError:
                pass
            self.home += 1
            self.failovers += 1
            time.sleep(RETRY_PAUSE_S)
        return False

    def run(self) -> None:
        interval = CLIENTS / RATE
        next_at = time.perf_counter()
        i = 0
        with httpx2.Client(timeout=15) as http:
            while not self.stop.is_set():
                resend = i % RESEND_EVERY == 0 and len(self.sent) > RESEND_DISTANCE
                record = (
                    self.sent[-RESEND_DISTANCE]
                    if resend
                    else {
                        "event_id": str(uuid.uuid4()),
                        "tenant_id": self.tenant,
                        "model": "claude-haiku-5-5",
                        "input_tokens": 100 + i * 7 % 900,
                        "output_tokens": i % 50,
                        "occurred_at": f"{DAY}T12:00:00Z",
                    }
                )
                started = time.perf_counter()
                if self.post(http, record):
                    self.acks.append((started, time.perf_counter() - started))
                    if resend:
                        self.resends += 1
                    else:
                        self.sent.append(record)
                elif not resend:
                    self.unacked.append(record)
                i += 1
                next_at = max(next_at + interval, time.perf_counter() - 1.0)
                time.sleep(max(0.0, next_at - time.perf_counter()))


class StackMemory:
    """Peak total memory of the crash stack's containers, from `docker stats`."""

    UNITS: ClassVar[dict[str, float]] = {
        "KiB": 1 / 1024,
        "MiB": 1.0,
        "GiB": 1024.0,
        "B": 1 / 1024**2,
    }

    def __init__(self) -> None:
        self.peak_mib = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @classmethod
    def mib(cls, value: str) -> float:
        for unit, factor in cls.UNITS.items():
            if value.endswith(unit):
                return float(value.removesuffix(unit)) * factor
        return 0.0

    def _run(self) -> None:
        while not self._stop.is_set():
            lines = run("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")
            total = sum(
                self.mib(usage.split("/")[0].strip())
                for name, usage in (line.split("\t") for line in lines.splitlines())
                if name.startswith(PROJECT)
            )
            self.peak_mib = max(self.peak_mib, total)

    def stop(self) -> float:
        self._stop.set()
        self._thread.join()
        return round(self.peak_mib)


def wait_in_sync(timeout: float = IN_SYNC_TIMEOUT_S) -> float | None:
    """Seconds until all three nodes are registered and every partition of every topic has a
    leader and all its replicas in sync.

    A node that is down for less than replica.lag.time.max.ms (30 s) never leaves the in-sync
    list, so this can pass at once; the cost of a kill shows in the write stall instead."""
    admin = AdminClient({"bootstrap.servers": SETTINGS.kafka_bootstrap})
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            md = admin.list_topics(timeout=5)
            partitions = [p for t in md.topics.values() for p in t.partitions.values()]
            if len(md.brokers) == len(BROKERS) and all(
                p.leader >= 0 and len(p.isrs) == len(p.replicas) for p in partitions
            ):
                return round(time.perf_counter() - started, 1)
        except Exception:  # noqa: S110 - the cluster is still settling: ask again
            pass
        time.sleep(0.5)
    return None


# A breaker runs beside the load until `stop` is set, and logs what it broke and when.
Breaker = Callable[[threading.Event, float, list[dict[str, Any]]], None]


def kill_broker(service: str, t0: float, log: list[dict[str, Any]]) -> None:
    [container] = containers(service)
    at = time.perf_counter() - t0
    sigkill(container, BROKER_DOWN_S)
    log.append(
        {
            "at_s": round(at, 1),
            "sigkill": service,
            "down_s": BROKER_DOWN_S,
            "healthy_after_restart_s": wait_in_sync(),
        }
    )


def break_brokers(stop: threading.Event, t0: float, log: list[dict[str, Any]]) -> None:
    n = 0
    while not stop.wait(BROKER_KILL_EVERY_S):
        kill_broker(BROKERS[n % len(BROKERS)], t0, log)
        n += 1


def scale_consumers(instances: int, t0: float, log: list[dict[str, Any]]) -> None:
    compose(
        "up",
        "--detach",
        "--no-deps",
        "--no-recreate",
        "--scale",
        f"totals={instances}",
        "--scale",
        f"ledger={instances}",
        "totals",
        "ledger",
    )
    log.append({"at_s": round(time.perf_counter() - t0, 1), "scaled_to": instances})


def break_consumers(stop: threading.Event, t0: float, log: list[dict[str, Any]]) -> None:
    scale_consumers(2, t0, log)
    n = 0
    while not stop.wait(CONSUMER_KILL_EVERY_S):
        if time.perf_counter() - t0 >= SCALE_DOWN_AT_S:
            scale_consumers(1, t0, log)
            return
        targets = [("totals", c) for c in containers("totals")]
        targets += [("ledger", c) for c in containers("ledger")]
        service, container = targets[n % len(targets)]
        log.append({"at_s": round(time.perf_counter() - t0, 1), "sigkill": service})
        sigkill(container)
        n += 1


def break_anything(stop: threading.Event, t0: float, log: list[dict[str, Any]]) -> None:
    rng = random.Random(7)  # noqa: S311 - a repeatable sequence of kills
    services = [*BROKERS, "gateway", "gateway-2", "gateway-3", "totals", "ledger"]
    while not stop.wait(MIXED_KILL_EVERY_S):
        service = rng.choice(services)
        if service in BROKERS:
            kill_broker(service, t0, log)
            continue
        log.append({"at_s": round(time.perf_counter() - t0, 1), "sigkill": service})
        sigkill(rng.choice(containers(service)))


def slow_database(stop: threading.Event, t0: float, log: list[dict[str, Any]]) -> None:
    if stop.wait(THROTTLE_AT_S):
        return
    [postgres] = containers("postgres")
    run("docker", "update", "--cpus", THROTTLE_CPUS, postgres)
    started = time.perf_counter() - t0
    time.sleep(THROTTLE_FOR_S)
    run("docker", "update", "--cpus", "0", postgres)  # 0 removes the limit
    log.append(
        {
            "throttle_from_s": round(started, 1),
            "throttle_to_s": round(time.perf_counter() - t0, 1),
            "cpus": float(THROTTLE_CPUS),
        }
    )


SCENARIOS: dict[str, Breaker] = {
    "brokers": break_brokers,
    "consumers": break_consumers,
    "mixed": break_anything,
    "slow-db": slow_database,
}


def ledger_rows(tenants: list[str]) -> dict[str, tuple[str, int, int]]:
    """event_id -> (tenant, Kafka partition, Kafka offset), for the given tenants."""
    with Database(SETTINGS) as db:
        rows = db.query(
            "SELECT event_id::text, tenant_id, kafka_partition, kafka_offset FROM usage_events "
            "WHERE tenant_id = ANY(CAST(:t AS text[]))",
            t=tenants,
        )
    return {e: (t, p, o) for e, t, p, o in rows}


def expected_totals(clients: list[Client]) -> Totals:
    totals: Totals = {}
    for c in clients:
        if c.sent:
            totals[f"{c.tenant}|{DAY}"] = (
                sum(r["input_tokens"] for r in c.sent),
                sum(r["output_tokens"] for r in c.sent),
                len(c.sent),
            )
    return totals


def order_violations(clients: list[Client], rows: dict[str, tuple[str, int, int]]) -> int:
    """Consecutive acknowledged records of one tenant that are not in increasing log order."""
    violations = 0
    for c in clients:
        positions = [rows[r["event_id"]][1:] for r in c.sent if r["event_id"] in rows]
        violations += sum(1 for a, b in itertools.pairwise(positions) if b <= a)
    return violations


def scenario(name: str, breaker: Breaker) -> dict[str, Any]:
    print(f"--- {name}", flush=True)
    run_id = uuid.uuid4().hex[:8]
    stop = threading.Event()
    clients = [Client(n, run_id, stop) for n in range(CLIENTS)]
    tenants = [c.tenant for c in clients]
    tail = TotalsTail(tenants)
    memory = StackMemory()
    log: list[dict[str, Any]] = []
    breaker_stop = threading.Event()

    t0 = time.perf_counter()
    breaker_thread = threading.Thread(target=breaker, args=(breaker_stop, t0, log))
    breaker_thread.start()
    for c in clients:
        c.start()
    time.sleep(LOAD_S)
    stop.set()
    for c in clients:
        c.join()
    breaker_stop.set()
    breaker_thread.join()  # a broker kill in progress finishes, in sync again, first

    expected = expected_totals(clients)
    times = wait_all(
        {
            "kafka": lambda: tail.view() == expected,
            "database": lambda: database_view(tenants) == expected,
        },
        timeout=CONVERGE_TIMEOUT_S,
    )
    rows = ledger_rows(tenants)
    tail.close()
    acked_ids = {r["event_id"] for c in clients for r in c.sent}
    known_ids = acked_ids | {r["event_id"] for c in clients for r in c.unacked}
    split = sum(1 for t in tenants if len({p for tt, p, _ in rows.values() if tt == t}) > 1)
    acks = [a for c in clients for a in c.acks]
    for b in log:  # the longest any client waited for a 202 on a send started near the kill
        if "sigkill" in b:
            lo = t0 + b["at_s"]
            waits = [s for at, s in acks if lo <= at < lo + STALL_WINDOW_S]
            b["longest_ack_ms"] = round(max(waits) * 1000) if waits else None
    result: dict[str, Any] = {
        **broker_settings(),
        "load_s": LOAD_S,
        "clients": CLIENTS,
        "target_rate_per_s": RATE,
        "acked_per_s": round(len(acks) / LOAD_S),
        "records_acked": len(acked_ids),
        "resends_acked": sum(c.resends for c in clients),
        "failovers": sum(c.failovers for c in clients),
        "unacked": sum(len(c.unacked) for c in clients),
        "breaks": log,
        "lost": len(acked_ids - rows.keys()),
        "extra": len(rows.keys() - known_ids),
        "order_violations": order_violations(clients, rows),
        "tenants_on_more_than_one_partition": split,
        "kafka": verdict(expected, tail.view(), times["kafka"]),
        "database": verdict(expected, database_view(tenants), times["database"]),
        "ack_latency_ms": percentiles_ms([s for _, s in acks]),
    }
    throttles = [b for b in log if "throttle_from_s" in b]
    if throttles:
        lo, hi = t0 + throttles[0]["throttle_from_s"], t0 + throttles[0]["throttle_to_s"]
        result["ack_latency_ms_while_throttled"] = percentiles_ms(
            [s for at, s in acks if lo <= at < hi]
        )
        result["ack_latency_ms_otherwise"] = percentiles_ms(
            [s for at, s in acks if not lo <= at < hi]
        )
    result["stack_peak_mib"] = memory.stop()
    result["passed"] = (
        result["lost"] == result["extra"] == result["unacked"] == result["order_violations"] == 0
        and result["kafka"]["exact"]
        and result["database"]["exact"]
    )
    print(json.dumps(result, indent=2), flush=True)
    return result


def ledger_fingerprint(database: str) -> tuple[Any, ...]:
    """Row counts and checksums of a ledger's events and totals."""
    pg = SETTINGS.postgres
    url = f"postgresql://{pg.user}:{pg.password}@{pg.host}:{pg.port}/{database}"
    with Database(dataclasses.replace(SETTINGS, database_url=url)) as db:
        [events] = db.query(
            "SELECT count(*), md5(coalesce(string_agg("
            "event_id::text || ':' || kafka_partition || ':' || kafka_offset || ':' ||"
            " input_tokens || ':' || output_tokens, ',' ORDER BY event_id), '')) "
            "FROM usage_events"
        )
        [totals] = db.query(
            "SELECT count(*), md5(coalesce(string_agg("
            "tenant_id || ':' || day || ':' || input_tokens || ':' || output_tokens || ':' ||"
            " events, ',' ORDER BY tenant_id, day), '')) FROM usage_totals"
        )
    return (*events, *totals)


def log_records() -> int:
    """Records in usage.events: the sum of its partitions' end offsets."""
    consumer = Consumer({"bootstrap.servers": SETTINGS.kafka_bootstrap, "group.id": "probe"})
    try:
        topic = SETTINGS.usage_topic
        partitions = consumer.list_topics(topic, timeout=10).topics[topic].partitions
        return sum(
            consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)[1]
            for p in partitions
        )
    finally:
        consumer.close()


def replay() -> dict[str, Any]:
    """Rebuild the ledger from offset 0 into the database `replay` and compare it with the live
    one. Needs a quiet stack: the live ledger must be up to date and nothing sending."""
    print("--- replay", flush=True)
    with Database(SETTINGS) as db:
        db.execute("DROP DATABASE IF EXISTS replay")
        db.execute("CREATE DATABASE replay")
    live = ledger_fingerprint(SETTINGS.postgres.database)
    records = log_records()
    pg = SETTINGS.postgres
    started = time.perf_counter()
    container = compose(
        "run",
        "--detach",
        "--no-deps",
        "--env",
        f"DATABASE_URL=postgresql://{pg.user}:{pg.password}@postgres:5432/replay",
        "--env",
        f"LEDGER_GROUP=replay-{uuid.uuid4().hex[:8]}",
        "ledger",
    ).strip()
    memory = StackMemory()
    try:
        seconds: float | None = None
        while time.perf_counter() - started < REPLAY_TIMEOUT_S:
            try:
                if ledger_fingerprint("replay") == live:
                    seconds = round(time.perf_counter() - started, 1)
                    break
            except Exception:  # noqa: S110 - schema not created yet
                pass
            time.sleep(1)
    finally:
        run("docker", "rm", "--force", container)
    replayed = ledger_fingerprint("replay")
    result = {
        "log_records": records,
        "ledger_rows": live[0],
        "tenant_days": live[2],
        "identical": replayed == live,
        "seconds": seconds,
        "records_per_s": round(records / seconds) if seconds else None,
        "stack_peak_mib": memory.stop(),
        "passed": replayed == live,
    }
    print(json.dumps(result, indent=2), flush=True)
    return result


def machine() -> dict[str, Any]:
    cpu = next(
        line.split(":", 1)[1].strip()
        for line in pathlib.Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name")
    )
    mem_kib = next(
        int(line.split()[1])
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    return {
        "host": socket.gethostname(),
        "cpu": cpu,
        "wsl_mem_mib": mem_kib // 1024,
        "kafka": "apache/kafka:4.3.1, 3 nodes, replication factor 3, min in-sync replicas 2",
    }


def broker_settings() -> dict[str, int]:
    """The failure-detection settings the running Kafka nodes were started with."""
    [container] = containers("kafka-1")
    env = dict(
        line.split("=", 1)
        for line in run(
            "docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container
        ).splitlines()
        if "=" in line
    )
    return {
        "broker_session_timeout_ms": int(env["KAFKA_BROKER_SESSION_TIMEOUT_MS"]),
        "broker_heartbeat_interval_ms": int(env["KAFKA_BROKER_HEARTBEAT_INTERVAL_MS"]),
    }


def main() -> None:
    wanted = sys.argv[1:] or [*SCENARIOS, "replay"]
    unknown = set(wanted) - {*SCENARIOS, "replay"}
    assert not unknown, f"unknown scenarios: {sorted(unknown)}"
    assert containers("kafka-3"), "start the crash stack first: make crash-stack-up"
    assert wait_in_sync() is not None, "the cluster is not fully in sync"

    # CRASH_RESULTS names another results folder, to keep runs with other settings apart.
    name = os.environ.get("CRASH_RESULTS", f"{date.today()}-{socket.gethostname()}-crash")
    out = pathlib.Path(__file__).parent / "results" / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    results: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    results |= {"date": str(date.today()), "machine": machine()}
    results.setdefault("scenarios", {})
    for name in wanted:
        if name == "replay":
            results["scenarios"][name] = replay()
        else:
            results["scenarios"][name] = scenario(name, SCENARIOS[name])
        path.write_text(json.dumps(results, indent=2) + "\n")  # keep what ran if a later one fails
    print(
        "\n".join(
            f"{n}: {'PASS' if r['passed'] else 'FAIL'}" for n, r in results["scenarios"].items()
        )
    )


if __name__ == "__main__":
    main()
