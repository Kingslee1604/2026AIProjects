"""Budget service: the one place that hands out a tenant's daily token budget.

Gateways ask for allowances (slices of budget) and approve calls locally from them
(eventplat.allowance). One SQL statement grants min(asked, limit - granted) and adds it to
what was granted, so concurrent requests from any number of gateways are never granted more
than the limit in total: Postgres serializes them on the tenant's row.

Tenants without a budget are unlimited. Days are UTC, as in the usage totals.

Run: uvicorn --factory eventplat.budget:create_app
"""

import threading
from collections.abc import Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from typing import Protocol

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from eventplat.config import Settings
from eventplat.db import Database

SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS budgets (
    tenant_id   text PRIMARY KEY,
    daily_limit bigint NOT NULL CHECK (daily_limit >= 0)
)
""",
    """
CREATE TABLE IF NOT EXISTS budget_grants (
    tenant_id  text NOT NULL,
    day        date NOT NULL,
    granted    bigint NOT NULL,
    last_grant bigint NOT NULL,
    PRIMARY KEY (tenant_id, day)
)
""",
)

# Both SET expressions read the row as it was before this update, and under concurrency
# Postgres re-reads the latest committed row before applying them. No row comes back for a
# tenant without a budget, which means unlimited.
GRANT = """
INSERT INTO budget_grants AS g (tenant_id, day, granted, last_grant)
SELECT tenant_id, CAST(:day AS date), least(CAST(:want AS bigint), daily_limit),
       least(CAST(:want AS bigint), daily_limit)
FROM budgets WHERE tenant_id = :tenant
ON CONFLICT (tenant_id, day) DO UPDATE SET
    granted = g.granted + greatest(0, least(
        CAST(:want AS bigint),
        (SELECT daily_limit FROM budgets WHERE tenant_id = :tenant) - g.granted)),
    last_grant = greatest(0, least(
        CAST(:want AS bigint),
        (SELECT daily_limit FROM budgets WHERE tenant_id = :tenant) - g.granted))
RETURNING last_grant
"""

GIVE_BACK = """
UPDATE budget_grants SET granted = greatest(0, granted - CAST(:tokens AS bigint))
WHERE tenant_id = :tenant AND day = CAST(:day AS date)
"""

SET_LIMIT = """
INSERT INTO budgets (tenant_id, daily_limit) VALUES (:tenant, CAST(:limit AS bigint))
ON CONFLICT (tenant_id) DO UPDATE SET daily_limit = EXCLUDED.daily_limit
"""

STATUS = """
SELECT b.daily_limit, coalesce(g.granted, 0)
FROM budgets b
LEFT JOIN budget_grants g ON g.tenant_id = b.tenant_id AND g.day = CAST(:day AS date)
WHERE b.tenant_id = :tenant
"""


class BudgetLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_limit: int = Field(ge=0)


class BudgetStatus(BaseModel):
    tenant_id: str
    day: date
    daily_limit: int
    granted: int


class AllowanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=64)
    day: date
    tokens: int = Field(gt=0)


class AllowanceGrant(BaseModel):
    granted: int | None  # None: the tenant has no budget, so it is unlimited


class Store(Protocol):
    def grant(self, tenant_id: str, day: date, tokens: int) -> int | None: ...
    def give_back(self, tenant_id: str, day: date, tokens: int) -> None: ...
    def set_limit(self, tenant_id: str, daily_limit: int) -> None: ...
    def status(self, tenant_id: str, day: date) -> tuple[int, int] | None: ...
    def close(self) -> None: ...


class PostgresStore:
    """One connection per request thread; a connection that failed is dropped and reopened."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._local = threading.local()
        self._all: list[Database] = []
        self._all_lock = threading.Lock()
        with Database(settings) as db:
            for statement in SCHEMA:
                db.execute(statement)

    @contextmanager
    def _db(self) -> Generator[Database]:
        db: Database | None = getattr(self._local, "db", None)
        if db is None:
            db = self._local.db = Database(self._settings)
            with self._all_lock:
                self._all.append(db)
        try:
            yield db
        except Exception:
            self._local.db = None
            with self._all_lock:
                self._all.remove(db)
            db.close()
            raise

    def grant(self, tenant_id: str, day: date, tokens: int) -> int | None:
        with self._db() as db:
            rows = db.query(GRANT, tenant=tenant_id, day=day, want=tokens)
        return int(rows[0][0]) if rows else None

    def give_back(self, tenant_id: str, day: date, tokens: int) -> None:
        with self._db() as db:
            db.execute(GIVE_BACK, tenant=tenant_id, day=day, tokens=tokens)

    def set_limit(self, tenant_id: str, daily_limit: int) -> None:
        with self._db() as db:
            db.execute(SET_LIMIT, tenant=tenant_id, limit=daily_limit)

    def status(self, tenant_id: str, day: date) -> tuple[int, int] | None:
        with self._db() as db:
            rows = db.query(STATUS, tenant=tenant_id, day=day)
        return (int(rows[0][0]), int(rows[0][1])) if rows else None

    def close(self) -> None:
        with self._all_lock:
            for db in self._all:
                db.close()
            self._all.clear()


def create_app(store_factory: Callable[[], Store] | None = None) -> FastAPI:
    make_store = store_factory or (lambda: PostgresStore(Settings.from_env()))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = make_store()
        yield
        app.state.store.close()

    app = FastAPI(title="eventplat budget", lifespan=lifespan)

    def store() -> Store:
        return app.state.store

    @app.put("/v1/budgets/{tenant_id}")
    def set_budget(tenant_id: str, limit: BudgetLimit) -> BudgetLimit:
        store().set_limit(tenant_id, limit.daily_limit)
        return limit

    @app.get("/v1/budgets/{tenant_id}/{day}")
    def get_budget(tenant_id: str, day: date) -> BudgetStatus:
        found = store().status(tenant_id, day)
        if found is None:
            raise HTTPException(404, "no budget: tenant is unlimited")
        return BudgetStatus(tenant_id=tenant_id, day=day, daily_limit=found[0], granted=found[1])

    @app.post("/v1/allowances")
    def grant(req: AllowanceRequest) -> AllowanceGrant:
        return AllowanceGrant(granted=store().grant(req.tenant_id, req.day, req.tokens))

    @app.post("/v1/allowances/return", status_code=204)
    def give_back(req: AllowanceRequest) -> Response:
        store().give_back(req.tenant_id, req.day, req.tokens)
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
