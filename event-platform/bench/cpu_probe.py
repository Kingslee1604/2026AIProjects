"""CPU each container spends per 1000 acknowledged records, for A/B tests of a setting.

    make up
    uv run --frozen python bench/cpu_probe.py [clients]

One 20 s closed-loop run through the gateway (bench/load.py's clients), reading each
container's cgroup CPU counter before and after. Throughput on this laptop moves 10-30% from
run to run; CPU per record moves much less, so it is the better number for comparing settings.
Run it three times per setting and compare the middle values. Used for ADR-0005.
"""

import subprocess
import sys
import time

import load

CONTAINERS = ("gateway", "kafka-1", "ledger", "totals")
DURATION_S = 20.0


def cpu_usec() -> dict[str, int]:
    """CPU time used so far by each container (cgroup v1, as Docker Desktop on WSL2 runs it)."""
    usage = {}
    for name in CONTAINERS:
        ns = subprocess.run(
            [
                "docker",
                "exec",
                f"eventplat-{name}-1",
                "cat",
                "/sys/fs/cgroup/cpuacct/cpuacct.usage",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        usage[name] = int(ns) // 1000
    return usage


def main() -> None:
    clients = int(sys.argv[1]) if len(sys.argv) > 1 else 32
    runner = load.Load(f"probe{int(time.time())}")
    before = cpu_usec()
    job = load.Job("/v1/usage", f"load-{runner.run_id}", clients, 0, 0, 0.0, DURATION_S, None)
    samples, cpu = runner.run(job)
    time.sleep(3)  # let the ledger and the totals service finish the last records
    after = cpu_usec()
    runner.pool.shutdown()
    acked = sum(1 for s in samples if s[2] == 202)
    summary = load.summarize(samples, DURATION_S, 202)
    per_1000 = {n: round((after[n] - before[n]) / acked) for n in CONTAINERS}  # usec/rec = ms/1000
    print(
        f"records/s {summary['per_s']}, ack p50 {summary['latency_ms']['p50']} ms, "
        f"CPU ms per 1000 records {per_1000}, laptop {cpu['laptop_cpu_busy_pct']}% busy"
    )


if __name__ == "__main__":
    main()
