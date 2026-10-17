"""Every provider call is persisted exactly once, under the caller's tenant (R1-10)."""

import asyncio
import json
import uuid

from pydantic import BaseModel
from sqlalchemy import text

from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.migrate import DEFAULT_ORG_ID
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.models import CallContext, Message, ModelGateway
from agentforge.models.providers import FakeProvider
from agentforge.models.recorder import DbRecorder

from .conftest import Database


class Answer(BaseModel):
    answer: str


def test_each_call_is_recorded_once_and_only_visible_to_its_org(database: Database) -> None:
    run_id = uuid.uuid4()
    context = CallContext(org_id=DEFAULT_ORG_ID, purpose="test", run_id=run_id)

    async def scenario() -> tuple[int, int]:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=2)
        sessions = create_sessionmaker(engine)
        provider = FakeProvider(script=["not json", json.dumps({"answer": "42"})])
        gateway = ModelGateway({"fake": provider}, recorder=DbRecorder(sessions))
        try:
            parsed, _ = await gateway.generate_structured(
                context, [Message("user", "What is 6 x 7?")], Answer, model="fake:m"
            )
            await gateway.embed(context, ["hello world"], model="fake:e")
            assert parsed.answer == "42"
            async with tenant_session(sessions, TenantContext(DEFAULT_ORG_ID)) as db:
                mine = (
                    await db.execute(
                        text("SELECT count(*) FROM app.llm_calls WHERE run_id = :r"), {"r": run_id}
                    )
                ).scalar_one()
            async with tenant_session(sessions, TenantContext(uuid.uuid4())) as db:
                other = (
                    await db.execute(
                        text("SELECT count(*) FROM app.llm_calls WHERE run_id = :r"), {"r": run_id}
                    )
                ).scalar_one()
            return mine, other
        finally:
            await engine.dispose()

    mine, other = asyncio.run(scenario())
    assert (mine, other) == (3, 0)  # invalid try + repaired try + one embedding; none cross-tenant

    owner = create_sync_engine(database.url("agentforge_owner"))
    with owner.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT status, attempt, purpose, request, response FROM app.llm_calls "
                "WHERE run_id = :r ORDER BY created_at"
            ),
            {"r": run_id},
        ).all()
    owner.dispose()
    assert [(r.status, r.attempt) for r in rows] == [("invalid_output", 1), ("ok", 2), ("ok", 1)]
    assert rows[0].request[0]["content"] == "What is 6 x 7?"
    assert rows[1].response == '{"answer": "42"}'
