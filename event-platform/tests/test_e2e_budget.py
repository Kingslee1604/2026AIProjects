"""End to end: budget service -> gateway allowances -> authorize, against the running stack.

Sixteen clients spend one tenant's budget through the gateway until it says 402. Each call
uses between half and all of what it reserved, and its usage record settles the reservation.
The tokens used by approved calls must not exceed the limit, and should come close to it.
"""

import random
import threading
import uuid
from datetime import UTC, datetime

import httpx2
import pytest

from eventplat.allowance import utc_today
from eventplat.config import Settings

pytestmark = pytest.mark.e2e

GATEWAY = "http://127.0.0.1:8100"
BUDGET = "http://127.0.0.1:8110"
LIMIT = 300_000
CLIENTS = 16
MAX_CALL = 4000


def test_approved_usage_stays_within_the_budget() -> None:
    tenant = f"budget-{uuid.uuid4().hex[:8]}"
    with httpx2.Client(base_url=BUDGET, timeout=10) as budget:
        assert budget.put(f"/v1/budgets/{tenant}", json={"daily_limit": LIMIT}).status_code == 200

    used: list[int] = []
    lock = threading.Lock()

    def client(seed: int) -> None:
        rng = random.Random(seed)
        with httpx2.Client(base_url=GATEWAY, timeout=30) as gw:
            while True:
                call, reserve = str(uuid.uuid4()), rng.randrange(100, MAX_CALL)
                reply = gw.post(
                    "/v1/authorize",
                    json={"event_id": call, "tenant_id": tenant, "max_tokens": reserve},
                )
                if reply.status_code == 402:
                    return
                assert reply.status_code == 200
                actual = rng.randrange(reserve // 2, reserve + 1)
                record = {
                    "event_id": call,
                    "tenant_id": tenant,
                    "model": "claude-haiku-5-5",
                    "input_tokens": actual,
                    "output_tokens": 0,
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
                assert gw.post("/v1/usage", json=record).status_code == 202
                with lock:
                    used.append(actual)

    threads = [threading.Thread(target=client, args=(seed,)) for seed in range(CLIENTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with httpx2.Client(base_url=BUDGET, timeout=10) as budget:
        status = budget.get(f"/v1/budgets/{tenant}/{utc_today()}").json()
    assert sum(used) <= LIMIT
    assert status["granted"] <= LIMIT
    # One gateway: what it still holds is less than one call's reservation.
    assert sum(used) > LIMIT - Settings.from_env().allowance_tokens - MAX_CALL

    # Usage is the bill: it is stored even when the tenant is over budget.
    with httpx2.Client(base_url=GATEWAY, timeout=30) as gw:
        over = {
            "event_id": str(uuid.uuid4()),
            "tenant_id": tenant,
            "model": "claude-haiku-5-5",
            "input_tokens": 10,
            "output_tokens": 0,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        assert gw.post("/v1/usage", json=over).status_code == 202
