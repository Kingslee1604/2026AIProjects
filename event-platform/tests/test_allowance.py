import random
import threading
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from eventplat.allowance import Allowances, BudgetUnavailable
from eventplat.events import UsageEvent
from eventplat.gateway import create_app


class FakeBudget:
    """The budget service's rule in memory: grant at most what is left, atomically."""

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self.limits = limits or {}
        self.granted: dict[tuple[str, str], int] = {}
        self.calls = 0
        self.down = False
        self._lock = threading.Lock()

    def grant(self, tenant_id: str, day: str, tokens: int) -> int | None:
        if self.down:
            raise BudgetUnavailable("down")
        with self._lock:
            self.calls += 1
            if tenant_id not in self.limits:
                return None
            already = self.granted.get((tenant_id, day), 0)
            grant = max(0, min(tokens, self.limits[tenant_id] - already))
            self.granted[(tenant_id, day)] = already + grant
            return grant

    def give_back(self, tenant_id: str, day: str, tokens: int) -> None:
        with self._lock:
            key = (tenant_id, day)
            self.granted[key] = max(0, self.granted.get(key, 0) - tokens)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.day = "2026-10-27"

    def __call__(self) -> float:
        return self.now


def usage(event_id: uuid.UUID, tokens: int, tenant: str = "acme") -> UsageEvent:
    return UsageEvent(
        event_id=event_id,
        tenant_id=tenant,
        model="claude-haiku-5-5",
        input_tokens=tokens,
        output_tokens=0,
        occurred_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


def allowances(budget: FakeBudget, slice_tokens: int = 1000, clock: Clock | None = None):
    c = clock or Clock()
    return Allowances(budget, slice_tokens, clock=c, today=lambda: c.day)


def test_approves_locally_from_one_slice() -> None:
    budget = FakeBudget({"acme": 10_000})
    a = allowances(budget)
    assert all(a.authorize(uuid.uuid4(), "acme", 100) for _ in range(10))
    assert budget.calls == 1


def test_denies_once_the_budget_is_granted_out() -> None:
    budget = FakeBudget({"acme": 2500})
    a = allowances(budget)
    approved = [a.authorize(uuid.uuid4(), "acme", 1000) for _ in range(4)]
    assert approved == [True, True, False, False]
    assert budget.granted[("acme", "2026-10-27")] == 2500


def test_settling_returns_unused_tokens() -> None:
    a = allowances(FakeBudget({"acme": 1000}))
    first = uuid.uuid4()
    assert a.authorize(first, "acme", 1000)
    assert not a.authorize(uuid.uuid4(), "acme", 600)
    a.settle(usage(first, 400))  # reserved 1000, used 400
    assert a.authorize(uuid.uuid4(), "acme", 600)


def test_overuse_becomes_debt_paid_from_the_next_grant() -> None:
    budget = FakeBudget({"acme": 3000})
    a = allowances(budget)
    call = uuid.uuid4()
    assert a.authorize(call, "acme", 1000)
    a.settle(usage(call, 1500))  # used 500 more than reserved
    assert a.authorize(uuid.uuid4(), "acme", 1000)
    assert not a.authorize(uuid.uuid4(), "acme", 1000)  # 1500 + 1000 used, 500 left


def test_retries_neither_reserve_nor_refund_twice() -> None:
    a = allowances(FakeBudget({"acme": 1000}))
    call = uuid.uuid4()
    assert a.authorize(call, "acme", 800)
    assert a.authorize(call, "acme", 800)  # retried authorize: same approval
    a.settle(usage(call, 100))
    a.settle(usage(call, 100))  # retried usage record: refunds nothing more
    assert a.authorize(uuid.uuid4(), "acme", 900)
    assert not a.authorize(uuid.uuid4(), "acme", 1)


def test_unlimited_tenant_is_rechecked_only_now_and_then() -> None:
    budget = FakeBudget()
    clock = Clock()
    a = allowances(budget, clock=clock)
    assert all(a.authorize(uuid.uuid4(), "free", 10**6) for _ in range(5))
    assert budget.calls == 1
    clock.now += 31
    assert a.authorize(uuid.uuid4(), "free", 1)
    assert budget.calls == 2


def test_fails_closed_when_the_budget_service_is_down() -> None:
    budget = FakeBudget({"acme": 10_000})
    a = allowances(budget)
    assert a.authorize(uuid.uuid4(), "acme", 1000)  # covered by the slice already held
    budget.down = True
    with pytest.raises(BudgetUnavailable):
        a.authorize(uuid.uuid4(), "acme", 1)


def test_a_new_day_starts_fresh_and_ignores_yesterdays_settlements() -> None:
    budget = FakeBudget({"acme": 1000})
    clock = Clock()
    a = allowances(budget, clock=clock)
    yesterday = uuid.uuid4()
    assert a.authorize(yesterday, "acme", 1000)
    clock.day = "2026-10-28"
    assert a.authorize(uuid.uuid4(), "acme", 1000)
    a.settle(usage(yesterday, 0))  # must not top up today's allowance
    assert not a.authorize(uuid.uuid4(), "acme", 1)


def test_release_gives_back_the_unreserved_allowance() -> None:
    budget = FakeBudget({"acme": 10_000})
    a = allowances(budget, slice_tokens=5000)
    assert a.authorize(uuid.uuid4(), "acme", 1000)
    a.release()
    assert budget.granted[("acme", "2026-10-27")] == 1000


def test_many_gateways_never_approve_more_than_the_limit() -> None:
    """4 gateways x 8 threads share one budget; each call uses up to what it reserved."""
    limit = 200_000
    budget = FakeBudget({"acme": limit})
    gateways = [allowances(budget, slice_tokens=5000) for _ in range(4)]
    used: list[int] = []
    used_lock = threading.Lock()

    def client(seed: int) -> None:
        rng = random.Random(seed)
        gateway = gateways[seed % len(gateways)]
        denials = 0
        while denials < 20:
            call, reserve = uuid.uuid4(), rng.randrange(100, 4000)
            if not gateway.authorize(call, "acme", reserve):
                denials += 1
                continue
            actual = rng.randrange(reserve // 2, reserve + 1)
            with used_lock:
                used.append(actual)
            gateway.settle(usage(call, actual))

    threads = [threading.Thread(target=client, args=(seed,)) for seed in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(used) <= limit
    assert budget.granted[("acme", "2026-10-27")] <= limit
    # Nothing is lost except what sits in the gateways' slices: at most a slice each.
    assert sum(used) >= limit - len(gateways) * 5000


def test_gateway_authorize_endpoint() -> None:
    budget = FakeBudget({"acme": 1000})

    class NullPublisher:
        def publish(self, event: UsageEvent) -> None:
            pass

        def close(self) -> None:
            pass

    app = create_app(NullPublisher, lambda: allowances(budget))
    with TestClient(app) as client:

        def ask(tokens: int, event_id: str | None = None) -> int:
            body = {
                "event_id": event_id or str(uuid.uuid4()),
                "tenant_id": "acme",
                "max_tokens": tokens,
            }
            return client.post("/v1/authorize", json=body).status_code

        call = str(uuid.uuid4())
        assert ask(1000, call) == 200
        assert ask(1) == 402
        settled = client.post(
            "/v1/usage",
            json={
                "event_id": call,
                "tenant_id": "acme",
                "model": "m",
                "input_tokens": 300,
                "output_tokens": 0,
                "occurred_at": "2026-10-27T10:00:00Z",
            },
        )
        assert settled.status_code == 202
        assert ask(700) == 200
        assert ask(0) == 422
        budget.down = True
        assert ask(1) == 503
