"""Gateway side of budget enforcement: approve calls locally, from allowances.

Before a model call, the app asks the gateway to authorize it (POST /v1/authorize) with the
call's event_id and its maximum tokens. The gateway reserves that many tokens from its local
allowance for the tenant, topping up from the budget service in slices of `slice_tokens`.
No network call is needed while the allowance covers the request.

The usage record for the call (same event_id) settles the reservation: unused tokens go back
into the allowance, and tokens used beyond the reservation are taken from it, even into
debt, which the next top-up pays off first.

Accounting is conservative: budget can be lost, but never created. A reservation that is never
settled (the usage went to another gateway, or the call never happened) expires as spent, and
a crashed gateway's allowance is gone with it. So the tokens approved across all gateways
never exceed what the budget service granted, and that never exceeds the limit.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from uuid import UUID

from eventplat.events import UsageEvent

log = logging.getLogger("eventplat.allowance")

RESERVATION_TTL_S = 900.0
UNLIMITED_RECHECK_S = 30.0
BUDGET_TIMEOUT_S = 2.0


class BudgetUnavailable(Exception):
    """The budget service could not be reached; the caller should retry."""


class BudgetClient(Protocol):
    def grant(self, tenant_id: str, day: str, tokens: int) -> int | None:
        """Up to `tokens` of the tenant's budget for `day`; None if the tenant is unlimited."""
        ...

    def give_back(self, tenant_id: str, day: str, tokens: int) -> None: ...


class HttpBudgetClient:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        req = request.Request(  # noqa: S310 - the budget service URL comes from settings
            f"{self._base}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=BUDGET_TIMEOUT_S) as resp:  # noqa: S310
                return json.loads(resp.read() or b"{}")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise BudgetUnavailable(str(exc)) from exc

    def grant(self, tenant_id: str, day: str, tokens: int) -> int | None:
        reply = self._post("/v1/allowances", {"tenant_id": tenant_id, "day": day, "tokens": tokens})
        granted = reply.get("granted")
        return granted if isinstance(granted, int) else None

    def give_back(self, tenant_id: str, day: str, tokens: int) -> None:
        self._post("/v1/allowances/return", {"tenant_id": tenant_id, "day": day, "tokens": tokens})


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass
class _Tenant:
    day: str
    balance: int = 0  # negative: tokens used beyond reservations, not yet covered by a grant
    unlimited_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class _Reservation:
    tenant_id: str
    day: str
    tokens: int
    expires: float


@dataclass
class AllowanceStats:
    """Counters for logs and benchmarks; updated without a shared lock, so approximate."""

    approved: int = 0
    denied: int = 0
    grant_calls: int = 0


class Allowances:
    def __init__(
        self,
        budget: BudgetClient,
        slice_tokens: int,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = utc_today,
    ) -> None:
        self._budget = budget
        self._slice = slice_tokens
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()  # guards the two dicts; never held across a network call
        self._tenants: dict[str, _Tenant] = {}
        self._reservations: dict[UUID, _Reservation] = {}
        self.stats = AllowanceStats()

    def _tenant(self, tenant_id: str, day: str) -> _Tenant:
        with self._lock:
            state = self._tenants.get(tenant_id)
            if state is None or state.day != day:  # a new UTC day starts from a fresh budget
                state = self._tenants[tenant_id] = _Tenant(day)
            return state

    def authorize(self, event_id: UUID, tenant_id: str, tokens: int) -> bool:
        """Reserve `tokens` for this call. False means the tenant's budget is used up.

        Raises BudgetUnavailable if a top-up was needed and the budget service is down: the
        gateway fails closed rather than approve calls it cannot account for.
        """
        now = self._clock()
        with self._lock:
            if event_id in self._reservations:  # a retried authorize: already approved
                return True
            self._expire(now)
        day = self._today()
        state = self._tenant(tenant_id, day)
        with state.lock:
            if state.unlimited_until > now:
                self.stats.approved += 1
                return True
            if state.balance < tokens:
                self.stats.grant_calls += 1
                granted = self._budget.grant(
                    tenant_id, day, max(self._slice, tokens - state.balance)
                )
                if granted is None:
                    state.unlimited_until = now + UNLIMITED_RECHECK_S
                    self.stats.approved += 1
                    return True
                state.balance += granted
            if state.balance < tokens:
                self.stats.denied += 1
                return False
            state.balance -= tokens
        with self._lock:
            self._reservations[event_id] = _Reservation(
                tenant_id, day, tokens, now + RESERVATION_TTL_S
            )
        self.stats.approved += 1
        return True

    def settle(self, event: UsageEvent) -> None:
        """Square the call's reservation with what it really used. Retries settle nothing."""
        with self._lock:
            reservation = self._reservations.pop(event.event_id, None)
        if reservation is None:
            return
        with self._lock:
            state = self._tenants.get(reservation.tenant_id)
        if state is None or state.day != reservation.day:  # that day's allowance is gone
            return
        with state.lock:
            state.balance += reservation.tokens - (event.input_tokens + event.output_tokens)

    def release(self) -> None:
        """On shutdown: hand today's unreserved allowance back to the budget service."""
        day = self._today()
        with self._lock:
            states = [(t, s) for t, s in self._tenants.items() if s.day == day]
        for tenant_id, state in states:
            with state.lock:
                spare, state.balance = max(state.balance, 0), min(state.balance, 0)
            if spare:
                try:
                    self._budget.give_back(tenant_id, day, spare)
                except BudgetUnavailable:
                    log.warning("could not return %d tokens for %s", spare, tenant_id)

    def _expire(self, now: float) -> None:
        """Drop unsettled reservations past their TTL. They stay spent (see module docstring).

        Every reservation gets the same TTL, so insertion order is expiry order: only the
        oldest entries need looking at.
        """
        while self._reservations:
            event_id, reservation = next(iter(self._reservations.items()))
            if reservation.expires > now:
                return
            del self._reservations[event_id]
