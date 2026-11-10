"""Budget enforcement under gateway crashes: approved usage must never exceed the limit.

    make chaos-up        # gateways on 8100, 8101, 8102
    make bench-budget

Clients spend one tenant's daily budget through three gateways (each client sticks to one
gateway, as a load balancer with session affinity would). Every few seconds one gateway is
SIGKILLed and restarted; its clients fail over to another gateway and retry with the same
event_id. Each call uses between half and all of what it reserved.

Checks:
- tokens used by approved calls <= the limit (the promise);
- the budget service never granted more than the limit;
- the ledger billed exactly the tokens used, once each, despite the kills.
Also reports how much budget the crashes stranded (granted, but never usable).

Raw results: bench/results/<date>-<host>-budget-chaos/results.json
"""

import json
import pathlib
import random
import socket
import subprocess
import threading
import time
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx2

from eventplat.allowance import utc_today
from eventplat.config import Settings
from eventplat.db import Database

BUDGET = "http://127.0.0.1:8110"
LIMIT = 3_000_000
CLIENTS = 24
MAX_CALL = 4000
KILL_EVERY_S = 3.0
DENIALS_TO_STOP = 5


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


class Gateways:
    """The gateway containers and their host ports."""

    def __init__(self) -> None:
        self.containers = docker(
            "compose", "--profile", "chaos", "ps", "--quiet", "gateway", "gateway-2", "gateway-3"
        ).split()
        assert len(self.containers) >= 2, "start the extra gateways first: make chaos-up"
        self._lock = threading.Lock()
        self._urls = {c: self._url(c) for c in self.containers}

    @staticmethod
    def _url(container: str) -> str:
        return "http://" + docker("port", container, "8000/tcp").split()[0]

    def url(self, index: int) -> str:
        with self._lock:
            return self._urls[self.containers[index % len(self.containers)]]

    def restart(self, container: str) -> None:
        docker("kill", "--signal", "KILL", container)
        docker("start", container)
        with self._lock:
            self._urls[container] = self._url(container)


class Client(threading.Thread):
    def __init__(self, seed: int, tenant: str, gateways: Gateways) -> None:
        super().__init__()
        self.rng = random.Random(seed)  # noqa: S311 - repeatable test data
        self.tenant = tenant
        self.gateways = gateways
        self.home = seed
        self.used: list[int] = []
        self.failovers = 0

    def post(self, path: str, body: dict[str, Any]) -> int:
        """POST to this client's gateway; on a dead gateway, fail over to the next one."""
        for _ in range(60):
            try:
                with httpx2.Client(base_url=self.gateways.url(self.home), timeout=10) as gw:
                    status = gw.post(path, json=body).status_code
                if status != 503:
                    return status
            except httpx2.TransportError:
                pass
            self.home += 1
            self.failovers += 1
            time.sleep(0.2)
        raise RuntimeError(f"{path}: no gateway answered")

    def run(self) -> None:
        denials = 0
        while denials < DENIALS_TO_STOP:
            call, reserve = str(uuid.uuid4()), self.rng.randrange(100, MAX_CALL)
            status = self.post(
                "/v1/authorize",
                {"event_id": call, "tenant_id": self.tenant, "max_tokens": reserve},
            )
            if status == 402:
                denials += 1
                continue
            assert status == 200, status
            denials = 0
            actual = self.rng.randrange(reserve // 2, reserve + 1)
            self.used.append(actual)
            record = {
                "event_id": call,
                "tenant_id": self.tenant,
                "model": "claude-haiku-5-5",
                "input_tokens": actual,
                "output_tokens": 0,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            assert self.post("/v1/usage", record) == 202


def billed(tenant: str) -> tuple[int, int]:
    with Database(Settings.from_env()) as db:
        [[rows, tokens]] = db.query(
            "SELECT count(*), coalesce(sum(input_tokens + output_tokens), 0) "
            "FROM usage_events WHERE tenant_id = :t",
            t=tenant,
        )
    return int(rows), int(tokens)


def main() -> None:
    tenant = f"chaos-{uuid.uuid4().hex[:8]}"
    with httpx2.Client(base_url=BUDGET, timeout=10) as budget:
        assert budget.put(f"/v1/budgets/{tenant}", json={"daily_limit": LIMIT}).status_code == 200
    gateways = Gateways()
    clients = [Client(seed, tenant, gateways) for seed in range(CLIENTS)]
    stop = threading.Event()
    kills = 0

    def chaos() -> None:
        nonlocal kills
        rng = random.Random(99)  # noqa: S311
        while not stop.wait(KILL_EVERY_S):
            gateways.restart(rng.choice(gateways.containers))
            kills += 1

    started = time.perf_counter()
    killer = threading.Thread(target=chaos)
    killer.start()
    for c in clients:
        c.start()
    for c in clients:
        c.join()
    stop.set()
    killer.join()
    seconds = time.perf_counter() - started

    used = sum(sum(c.used) for c in clients)
    calls = sum(len(c.used) for c in clients)
    with httpx2.Client(base_url=BUDGET, timeout=10) as budget:
        granted = budget.get(f"/v1/budgets/{tenant}/{utc_today()}").json()["granted"]
    deadline = time.monotonic() + 60
    rows, tokens = billed(tenant)
    while (rows, tokens) != (calls, used) and time.monotonic() < deadline:
        time.sleep(1)
        rows, tokens = billed(tenant)

    results = {
        "date": str(date.today()),
        "gateways": len(gateways.containers),
        "allowance_tokens": Settings.from_env().allowance_tokens,
        "limit": LIMIT,
        "clients": CLIENTS,
        "seconds": round(seconds, 1),
        "sigkills": kills,
        "failovers": sum(c.failovers for c in clients),
        "approved_calls": calls,
        "used_tokens": used,
        "within_limit": used <= LIMIT,
        "granted_tokens": granted,
        "granted_within_limit": granted <= LIMIT,
        "stranded_tokens": LIMIT - used,
        "stranded_pct": round(100 * (LIMIT - used) / LIMIT, 2),
        "ledger": {"rows": rows, "tokens": tokens, "exact": (rows, tokens) == (calls, used)},
    }
    out = (
        pathlib.Path(__file__).parent
        / "results"
        / f"{date.today()}-{socket.gethostname()}-budget-chaos"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
