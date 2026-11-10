"""Throughput and latency of the dev stack, for the README.

    make up
    make bench-load

The dev stack: one Kafka node, one gateway (one uvicorn process), the budget service, the
ledger, the totals service and Postgres, each container capped at 256 MB.

1. Saturation (closed loop): N clients each send their next record as soon as the last one is
   acknowledged, for N from 8 to 128. Gives the most records per second one gateway acks.
2. Steady rates (open loop): clients send on a schedule at 25%, 50% and 80% of that maximum.
   For each rate: the gateway's ack latency (the record is on Kafka, acks=all) and the
   end-to-end latency until the ledger has written the row to Postgres.
3. Authorize: /v1/authorize latency when the gateway's local allowance covers the call, and
   when every call has to top up from the budget service.
4. Memory: total of the stack's containers at idle, and the peak during the run.
5. Three gateways (only if `make chaos-up` started the two extra ones): saturation again,
   with clients spread over all three.

The laptop's speed varies run to run (other programs, a hybrid CPU with fast and slow cores),
so saturation rates move by 10-30% between runs; compare runs, not single numbers.

Load comes from several processes, so the client's own GIL is not the limit; client CPU is
recorded anyway, and so is the laptop's.

End-to-end latency is the ledger row's recorded_at (Postgres now(): when the batch's
transaction began) minus occurred_at, which the client sets from its clock just before sending.
Both read the WSL2 kernel clock; the offset between them is measured and subtracted.

Raw results: bench/results/<date>-<host>-load/results.json
"""

import json
import multiprocessing
import os
import pathlib
import socket
import statistics
import subprocess
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import httpx2

from eventplat.batching import LINGER_S
from eventplat.config import Settings
from eventplat.db import Database

SETTINGS = Settings.from_env()
ROOT = pathlib.Path(__file__).parent.parent
GATEWAY = "http://127.0.0.1:8100"
EXTRA_GATEWAYS = ("http://127.0.0.1:8101", "http://127.0.0.1:8102")  # make chaos-up
BUDGET = "http://127.0.0.1:8110"
PROJECT = "eventplat-"

PROCS = 4
WARMUP_S = 3.0
SATURATION_CLIENTS, SATURATION_S = (8, 16, 32, 64, 128), 20.0
STEADY_SHARES, STEADY_CLIENTS, STEADY_S = (0.25, 0.5, 0.8), 64, 30.0
AUTHORIZE_CLIENTS, AUTHORIZE_S = (1, 16), 15.0
LEDGER_TIMEOUT_S = 180.0
UNLIMITED = 10**15

Sample = tuple[float, float, int]  # (seconds since start, latency in seconds, HTTP status)


@dataclass(frozen=True)
class Job:
    """Clients `first`..`first+count-1` of a phase; one Job runs in one process."""

    path: str  # /v1/usage or /v1/authorize
    prefix: str  # tenant ids are f"{prefix}-{client number}"
    clients: int  # in the whole phase, across processes
    first: int
    count: int
    start_at: float  # wall clock, so every process starts together
    duration_s: float
    interval_s: float | None  # per client; None: closed loop
    max_tokens: int = 0
    gateways: tuple[str, ...] = (GATEWAY,)  # client n uses gateways[n % len(gateways)]


def _client(job: Job, n: int, samples: list[Sample]) -> None:
    tenant = f"{job.prefix}-{n}"
    end = job.start_at + job.duration_s
    next_at = job.start_at + (job.interval_s or 0.0) * n / job.clients  # spread the schedule
    with httpx2.Client(base_url=job.gateways[n % len(job.gateways)], timeout=30) as http:
        while True:
            if job.interval_s is not None:
                time.sleep(max(0.0, next_at - time.time()))
            elif time.time() < job.start_at:
                time.sleep(job.start_at - time.time())
            if time.time() >= end:
                return
            if job.path == "/v1/usage":
                body: dict[str, Any] = {
                    "event_id": str(uuid.uuid4()),
                    "tenant_id": tenant,
                    "model": "claude-haiku-5-5",
                    "input_tokens": 250,
                    "output_tokens": 40,
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
            else:
                body = {
                    "event_id": str(uuid.uuid4()),
                    "tenant_id": tenant,
                    "max_tokens": job.max_tokens,
                }
            sent = time.time()
            started = time.perf_counter()
            try:
                status = http.post(job.path, json=body).status_code
            except httpx2.TransportError:
                status = 0
            samples.append((sent - job.start_at, time.perf_counter() - started, status))
            if job.interval_s is not None:
                # Fall behind by at most a second, so a stall is not followed by a burst.
                next_at = max(next_at + job.interval_s, time.time() - 1.0)


def run_job(job: Job) -> tuple[list[Sample], float]:
    """One process: its clients as threads. Returns the samples and the CPU seconds used."""
    samples: list[Sample] = []
    cpu = time.process_time()
    threads = [
        threading.Thread(target=_client, args=(job, n, samples), daemon=True)
        for n in range(job.first, job.first + job.count)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return samples, time.process_time() - cpu


class Load:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.start_at = 0.0
        self.pool = ProcessPoolExecutor(PROCS, mp_context=multiprocessing.get_context("spawn"))
        list(self.pool.map(abs, range(PROCS)))  # start the workers before anything is timed

    def run(self, template: Job) -> tuple[list[Sample], dict[str, Any]]:
        """Run a phase. Sample times count from self.start_at, set here."""
        start_at = self.start_at = time.time() + 1.0
        per = -(-template.clients // PROCS)
        jobs = [
            replace(template, first=i, count=min(per, template.clients - i), start_at=start_at)
            for i in range(0, template.clients, per)
        ]
        cpu_before = host_cpu()
        results = list(self.pool.map(run_job, jobs))
        cpu_after = host_cpu()
        samples = [s for result, _ in results for s in result]
        busy = (cpu_after[0] - cpu_before[0]) / max(cpu_after[1] - cpu_before[1], 1)
        return samples, {
            "client_cpu_cores": round(sum(c for _, c in results) / template.duration_s, 2),
            "laptop_cpu_busy_pct": round(busy * 100),
        }


def host_cpu() -> tuple[int, int]:
    """(busy, total) jiffies of the WSL2 VM, which also runs the containers."""
    fields = [int(x) for x in pathlib.Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
    idle = fields[3] + fields[4]
    return sum(fields) - idle, sum(fields)


def percentiles_ms(seconds: list[float]) -> dict[str, float | None]:
    if len(seconds) < 2:
        return {"p50": None, "p90": None, "p99": None, "max": None}
    q = statistics.quantiles(seconds, n=100)
    return {
        "p50": round(q[49] * 1000, 1),
        "p90": round(q[89] * 1000, 1),
        "p99": round(q[98] * 1000, 1),
        "max": round(max(seconds) * 1000, 1),
    }


def summarize(samples: list[Sample], duration_s: float, ok: int) -> dict[str, Any]:
    timed = [s for s in samples if s[0] >= WARMUP_S]
    good = [s for s in timed if s[2] == ok]
    statuses: dict[str, int] = {}
    for _, _, status in samples:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    return {
        "requests": len(samples),
        "statuses": statuses,
        "per_s": round(len(good) / (duration_s - WARMUP_S)),
        "latency_ms": percentiles_ms([s[1] for s in good]),
    }


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, cwd=ROOT).stdout


class StackMemory:
    """Total memory of the dev stack's containers, from `docker stats`."""

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

    @classmethod
    def sample(cls) -> dict[str, float]:
        lines = run("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")
        found: dict[str, float] = {}
        for line in lines.splitlines():
            name, usage = line.split("\t")
            if name.startswith(PROJECT) and not name.startswith("eventplat-crash"):
                value = usage.split("/")[0].strip()
                unit = next(u for u in cls.UNITS if value.endswith(u))
                found[name] = float(value.removesuffix(unit)) * cls.UNITS[unit]
        return found

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_mib = max(self.peak_mib, sum(self.sample().values()))

    def stop(self) -> int:
        self._stop.set()
        self._thread.join()
        return round(self.peak_mib)


def clock_offset_s(db: Database) -> dict[str, float]:
    """Postgres clock minus ours, from the round trip with the smallest delay."""
    best: tuple[float, float] | None = None
    for _ in range(50):
        t0 = time.time()
        db_now = float(db.query("SELECT extract(epoch FROM clock_timestamp())")[0][0])
        t1 = time.time()
        if best is None or t1 - t0 < best[0]:
            best = (t1 - t0, db_now - (t0 + t1) / 2)
    assert best is not None
    return {"offset_ms": round(best[1] * 1000, 2), "uncertainty_ms": round(best[0] * 500, 2)}


def ledger_latency(db: Database, prefix: str, acked: int, since: float, offset_s: float):
    """Wait until the ledger has every acked record of the phase, then their latencies."""
    deadline = time.monotonic() + LEDGER_TIMEOUT_S
    like = f"{prefix}-%"
    while True:
        count = int(
            db.query("SELECT count(*) FROM usage_events WHERE tenant_id LIKE :p", p=like)[0][0]
        )
        if count >= acked or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    rows = db.query(
        "SELECT extract(epoch FROM recorded_at - occurred_at) FROM usage_events"
        " WHERE tenant_id LIKE :p AND occurred_at >= to_timestamp(:since)",
        p=like,
        since=since,
    )
    return {
        "in_ledger": count,
        "acked": acked,
        "latency_ms": percentiles_ms([float(r[0]) - offset_s for r in rows]),
    }


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
        "wsl_cpus": len(pathlib.Path("/proc/cpuinfo").read_text().split("processor\t:")) - 1,
        "wsl_mem_mib": mem_kib // 1024,
        "stack": "1 Kafka node (apache/kafka:4.3.1), 1 gateway (1 uvicorn process with uvloop "
        "and httptools, no access log), budget, ledger, totals, postgres:18.4; each app "
        "container capped at 256 MB",
        "load": f"{PROCS} client processes on the same laptop",
    }


def saturation(
    load: Load, gateways: tuple[str, ...], client_counts: tuple[int, ...]
) -> list[dict[str, Any]]:
    steps = []
    for clients in client_counts:
        prefix = f"load-{load.run_id}-sat{len(gateways)}gw{clients}"
        job = Job("/v1/usage", prefix, clients, 0, 0, 0.0, SATURATION_S, None, gateways=gateways)
        samples, cpu = load.run(job)
        step = {"clients": clients, **summarize(samples, SATURATION_S, 202), **cpu}
        print(json.dumps(step), flush=True)
        steps.append(step)
    return steps


def steady(load: Load, db: Database, max_rate: int, offset_s: float) -> list[dict[str, Any]]:
    steps = []
    for share in STEADY_SHARES:
        rate = round(max_rate * share / 10) * 10
        prefix = f"load-{load.run_id}-steady{round(share * 100)}"
        job = Job("/v1/usage", prefix, STEADY_CLIENTS, 0, 0, 0.0, STEADY_S, STEADY_CLIENTS / rate)
        samples, cpu = load.run(job)
        acked = sum(1 for s in samples if s[2] == 202)
        since = load.start_at + WARMUP_S
        step = {
            "target_per_s": rate,
            "share_of_max": share,
            "gateway": summarize(samples, STEADY_S, 202),
            "ledger": ledger_latency(db, prefix, acked, since, offset_s),
            **cpu,
        }
        print(json.dumps(step), flush=True)
        steps.append(step)
    return steps


def authorize(load: Load, slice_tokens: int) -> list[dict[str, Any]]:
    steps = []
    with httpx2.Client(base_url=BUDGET, timeout=10) as budget:
        for path_name, max_tokens in (
            ("local allowance", 100),
            ("top-up every call", slice_tokens),
        ):
            for clients in AUTHORIZE_CLIENTS:
                prefix = f"load-{load.run_id}-auth{max_tokens}-{clients}"
                for n in range(clients):
                    budget.put(f"/v1/budgets/{prefix}-{n}", json={"daily_limit": UNLIMITED})
                job = Job(
                    "/v1/authorize", prefix, clients, 0, 0, 0.0, AUTHORIZE_S, None, max_tokens
                )
                samples, cpu = load.run(job)
                step = {
                    "path": path_name,
                    "max_tokens": max_tokens,
                    "clients": clients,
                    **summarize(samples, AUTHORIZE_S, 200),
                    **cpu,
                }
                print(json.dumps(step), flush=True)
                steps.append(step)
    return steps


def healthy(url: str) -> bool:
    try:
        return httpx2.get(f"{url}/healthz", timeout=2).status_code == 200
    except httpx2.TransportError:
        return False


def gateway_env() -> dict[str, str]:
    container = run("docker", "compose", "ps", "--quiet", "gateway").split()[0]
    lines = run(
        "docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container
    ).splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def main() -> None:
    env = gateway_env()
    slice_tokens = int(env["ALLOWANCE_TOKENS"])
    run_id = uuid.uuid4().hex[:8]
    with Database(SETTINGS) as db:
        offset = clock_offset_s(db)
        idle = StackMemory.sample()
        memory = StackMemory()
        memory.start()
        load = Load(run_id)
        three: list[dict[str, Any]] = []
        try:
            sat = saturation(load, (GATEWAY,), SATURATION_CLIENTS)
            best = max(sat, key=lambda s: s["per_s"])
            steady_steps = steady(load, db, best["per_s"], offset["offset_ms"] / 1000)
            auth = authorize(load, slice_tokens)
            if all(healthy(url) for url in EXTRA_GATEWAYS):
                three = saturation(load, (GATEWAY, *EXTRA_GATEWAYS), SATURATION_CLIENTS[2:])
        finally:
            load.pool.shutdown()
            peak = memory.stop()
    result = {
        "run_id": run_id,
        "machine": machine(),
        "settings": {
            "allowance_slice_tokens": slice_tokens,
            "consumer_session_timeout_ms": int(env["CONSUMER_SESSION_TIMEOUT_MS"]),
            "producer": "acks=all, idempotent, linger.ms=5, no compression",
            "consumer_batch": f"up to 500 records, {LINGER_S} s linger after the first",
        },
        "clock": offset,
        "memory_mib": {
            "idle_total": round(sum(idle.values())),
            "idle_by_container": {k: round(v) for k, v in sorted(idle.items())},
            "peak_total_under_load": peak,
        },
        "saturation": sat,
        "max_per_s": best["per_s"],
        "steady": steady_steps,
        "authorize": auth,
        "three_gateways": three,
        "three_gateways_max_per_s": max((s["per_s"] for s in three), default=None),
    }
    # LOAD_RESULTS names another results folder, to keep repeated runs apart.
    name = os.environ.get("LOAD_RESULTS", f"{date.today()}-{socket.gethostname()}-load")
    out = pathlib.Path(__file__).parent / "results" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("max_per_s", "memory_mib", "clock")}, indent=2))
    print(f"results: {out / 'results.json'}")


if __name__ == "__main__":
    main()
