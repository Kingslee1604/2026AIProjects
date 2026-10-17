"""Ask -> worker answers -> verified claims persisted, on real Postgres with the fake provider
(R1-16, R1-17): API contract, job processing, links to llm_calls, immutable agent versions,
abstention, and tenant isolation."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agentforge.agents.registry import AgentRegistry, load_definitions
from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.auth.users import create_user
from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.migrate import DEFAULT_ORG_ID
from agentforge.models.providers import FakeProvider

from ..qa_fakes import grounded_responder
from .conftest import PASSWORD, Database, UserFactory, login, settings_for
from .test_knowledge_flow import POLICY_MD, drain, new_kb, services_of, upload


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    # Fake embeddings are bag-of-words vectors, so distances run higher than with a real model.
    return create_app(
        settings_for(
            database, model_provider="fake", blob_root=tmp_path / "blobs", qa_max_distance=0.85
        )
    )


@pytest.fixture
def asker(app: FastAPI, make_user: UserFactory) -> Iterator[tuple[TestClient, str, str]]:
    with TestClient(app) as client:
        csrf = login(client, make_user(Role.MEMBER))
        kb = new_kb(client, csrf)
        upload(client, csrf, kb, "policy.md", POLICY_MD)
        assert drain(client) == 1
        fake = services_of(client).gateway.provider("fake")
        assert isinstance(fake, FakeProvider)
        fake.responder = grounded_responder
        yield client, csrf, kb


def ask(client: TestClient, csrf: str, kb: str, question: str) -> str:
    response = client.post(
        "/v1/answers",
        json={"question": question, "knowledge_base_ids": [kb]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    return str(response.json()["id"])


def answer_queue(client: TestClient) -> int:
    svc = services_of(client)
    processed = 0
    while client.portal.call(svc.qa.process_next_job):  # type: ignore[union-attr]
        processed += 1
    return processed


def test_ask_is_answered_with_verified_citations(
    database: Database, asker: tuple[TestClient, str, str]
) -> None:
    client, csrf, kb = asker
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")
    assert client.get(f"/v1/answers/{answer_id}").json()["status"] == "pending"

    assert answer_queue(client) == 1
    body: dict[str, Any] = client.get(f"/v1/answers/{answer_id}").json()
    assert body["status"] == "answered", (body["reason"], body["claims"], body["evidence"])
    assert body["confidence"] == "high"
    assert "ninety days" in body["answer"]
    [claim] = body["claims"]
    assert claim["status"] == "verified"
    assert claim["kept"]
    assert claim["citations"] == ["E1"]
    cited = next(e for e in body["evidence"] if e["id"] == "E1")
    assert cited["filename"] == "policy.md"
    assert cited["section_path"][-1] == "Logging"
    assert set(body["agent_versions"]) == {"drafter", "relevance"}
    assert body["completed_at"] is not None
    assert body["timings"]["budget"]["llm_calls"] == 2  # draft, then the relevance check
    assert next(a["id"] for a in client.get("/v1/answers").json()) == answer_id

    # Every model call made for this answer (query embedding, draft, relevance) is linked to it.
    owner = create_sync_engine(database.url("agentforge_owner"))
    with owner.connect() as connection:
        purposes = (
            connection.execute(
                text("SELECT purpose FROM app.llm_calls WHERE run_id = :r ORDER BY created_at"),
                {"r": answer_id},
            )
            .scalars()
            .all()
        )
        jobs = (
            connection.execute(
                text("SELECT status FROM queue.answer_jobs WHERE answer_id = :r"), {"r": answer_id}
            )
            .scalars()
            .all()
        )
    owner.dispose()
    assert purposes == ["retrieve", "draft", "relevance"]
    assert jobs == ["done"]


def test_unanswerable_question_abstains(asker: tuple[TestClient, str, str]) -> None:
    client, csrf, kb = asker
    answer_id = ask(client, csrf, kb, "Quarterly volcano ferret budgets?")
    answer_queue(client)
    body = client.get(f"/v1/answers/{answer_id}").json()
    assert body["status"] == "insufficient_evidence", body["reason"]
    assert body["answer"] == "Insufficient evidence in the knowledge base."
    assert body["claims"] == []


def test_model_outage_retries_later_then_succeeds(asker: tuple[TestClient, str, str]) -> None:
    client, csrf, kb = asker
    fake = services_of(client).gateway.provider("fake")
    assert isinstance(fake, FakeProvider)
    fake.script = ["unavailable"]
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")
    answer_queue(client)
    body = client.get(f"/v1/answers/{answer_id}").json()
    assert body["status"] == "pending"
    assert "ModelUnavailableError" in body["error"]


def test_validation_and_unknown_knowledge_bases(asker: tuple[TestClient, str, str]) -> None:
    client, csrf, _ = asker
    headers = {"X-CSRF-Token": csrf}
    missing = client.post(
        "/v1/answers",
        json={"question": "Anything?", "knowledge_base_ids": [str(uuid.uuid4())]},
        headers=headers,
    )
    assert missing.status_code == 404
    empty = client.post(
        "/v1/answers", json={"question": "Hi?", "knowledge_base_ids": []}, headers=headers
    )
    assert empty.status_code == 422
    assert client.get(f"/v1/answers/{uuid.uuid4()}").status_code == 404


def test_other_organizations_cannot_read_or_use_answers(
    database: Database, app: FastAPI, asker: tuple[TestClient, str, str]
) -> None:
    client, csrf, kb = asker
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")

    other_org = uuid.uuid4()
    owner = create_sync_engine(database.url("agentforge_owner"))
    with owner.begin() as connection:
        connection.execute(
            text("INSERT INTO app.organizations (id, name, slug) VALUES (:o, 'Other', :s)"),
            {"o": other_org, "s": f"other-{other_org.hex[:8]}"},
        )
    owner.dispose()
    email = f"outsider-{uuid.uuid4().hex[:8]}@example.test"

    async def make_outsider() -> None:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        await create_user(
            create_sessionmaker(engine),
            org_id=other_org,
            email=email,
            password=PASSWORD,
            role=Role.ADMIN,
        )
        await engine.dispose()

    asyncio.run(make_outsider())
    with TestClient(app) as outsider:
        outsider_csrf = login(outsider, email)
        assert outsider.get(f"/v1/answers/{answer_id}").status_code == 404
        assert outsider.get("/v1/answers").json() == []
        refused = outsider.post(
            "/v1/answers",
            json={"question": "How long are logs kept?", "knowledge_base_ids": [kb]},
            headers={"X-CSRF-Token": outsider_csrf},
        )
        assert refused.status_code == 404


def test_agent_versions_are_immutable_and_appended_on_change(
    database: Database, tmp_path: Path
) -> None:
    source = load_definitions()["drafter"]
    changed = source.model_copy(update={"temperature": 0.3})

    async def scenario() -> tuple[int, int, int]:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        sessions = create_sessionmaker(engine)
        try:
            first = await AgentRegistry(sessions, {"drafter": source}).version(
                DEFAULT_ORG_ID, "drafter"
            )
            same = await AgentRegistry(sessions, {"drafter": source}).version(
                DEFAULT_ORG_ID, "drafter"
            )
            second = await AgentRegistry(sessions, {"drafter": changed}).version(
                DEFAULT_ORG_ID, "drafter"
            )
            assert first.id == same.id
            assert second.id != first.id
            async with sessions() as db, db.begin():
                await db.execute(
                    text("SELECT set_config('app.org_id', :o, true)"), {"o": str(DEFAULT_ORG_ID)}
                )
                with pytest.raises(DBAPIError, match="permission denied"):
                    await db.execute(
                        text("UPDATE app.agent_versions SET definition = '{}' WHERE id = :i"),
                        {"i": first.id},
                    )
            return first.version, same.version, second.version
        finally:
            await engine.dispose()

    first, same, second = asyncio.run(scenario())
    assert same == first
    assert second == first + 1


def test_answer_calls_show_what_the_model_was_sent(asker: tuple[TestClient, str, str]) -> None:
    client, csrf, kb = asker
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")
    answer_queue(client)
    calls = client.get(f"/v1/answers/{answer_id}/calls").json()
    assert [c["purpose"] for c in calls] == ["retrieve", "draft", "relevance"]
    draft = calls[1]
    assert draft["status"] == "ok"
    assert draft["model"].startswith("fake:")
    assert "<evidence" in draft["request"][-1]["content"]
    assert "claims" in draft["response"]
    assert client.get(f"/v1/answers/{uuid.uuid4()}/calls").status_code == 404


def test_answers_stored_before_newer_claim_fields_still_load(
    database: Database, asker: tuple[TestClient, str, str]
) -> None:
    """Regression: answers saved on 2026-10-13 lack quote_supports_claim and drafted_text."""
    client, csrf, kb = asker
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")
    answer_queue(client)
    owner = create_sync_engine(database.url("agentforge_owner"))
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE app.qa_answers SET claims = (SELECT jsonb_agg("
                "c - 'drafted_text' - 'quote_supports_claim') "
                "FROM jsonb_array_elements(claims) c) WHERE id = :i"
            ),
            {"i": answer_id},
        )
    owner.dispose()
    [claim] = client.get(f"/v1/answers/{answer_id}").json()["claims"]
    assert claim["drafted_text"] == claim["text"]
    assert claim["quote_supports_claim"] is False
    assert client.get("/v1/answers").status_code == 200
