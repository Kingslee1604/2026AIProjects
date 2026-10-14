"""Hard deletion of documents and knowledge bases (NFR-13, R1-41): rows, vectors, blobs, queued
jobs, and copies of the text inside answers and model-call logs; audited; tenant-scoped."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine
from agentforge.qa.purge import REDACTED

from .conftest import Database, UserFactory, login, settings_for
from .test_knowledge_flow import POLICY_MD, drain, new_kb, services_of, upload
from .test_qa_flow import answer_queue, ask

SECOND_MD = b"# Backup Policy\n\nBackups are taken nightly and kept for thirty five days.\n"


@pytest.fixture
def blob_root(tmp_path: Path) -> Path:
    return tmp_path / "blobs"


@pytest.fixture
def app(database: Database, blob_root: Path) -> FastAPI:
    return create_app(
        settings_for(database, model_provider="fake", blob_root=blob_root, qa_max_distance=0.85)
    )


@pytest.fixture
def writer(app: FastAPI, make_user: UserFactory) -> Iterator[tuple[TestClient, str]]:
    with TestClient(app) as client:
        yield client, login(client, make_user(Role.MEMBER))


def blobs(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def owner_query(database: Database, sql: str, **params: Any) -> list[Any]:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.connect() as connection:
            return list(connection.execute(text(sql), params).all())
    finally:
        engine.dispose()


def residuals(client: TestClient, document_ids: list[UUID]) -> dict[str, int]:
    svc = services_of(client)
    principal_org = owner_org(client)
    return client.portal.call(svc.knowledge.residuals, principal_org, document_ids)  # type: ignore[union-attr]


def owner_org(client: TestClient) -> UUID:
    return UUID(client.get("/v1/auth/session").json()["org_id"])


def test_deleting_a_document_removes_everything_derived_from_it(
    database: Database, writer: tuple[TestClient, str], blob_root: Path
) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    document = upload(client, csrf, kb, "policy.md", POLICY_MD)
    keep = upload(client, csrf, kb, "backup.md", SECOND_MD)
    drain(client)
    answer_id = ask(client, csrf, kb, "How long are access logs retained?")
    answer_queue(client)
    before = client.get(f"/v1/answers/{answer_id}").json()
    assert before["status"] == "answered"
    assert len(blobs(blob_root)) == 2

    response = client.delete(f"/v1/documents/{document['id']}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204, response.text

    doc_id = UUID(str(document["id"]))
    assert residuals(client, [doc_id]) == {
        "documents": 0,
        "chunks": 0,
        "orphan_embeddings": 0,
        "jobs": 0,
    }
    assert [p.name for p in blobs(blob_root)] == [str(keep["id"])]
    names = [d["filename"] for d in client.get(f"/v1/knowledge-bases/{kb}/documents").json()]
    assert names == ["backup.md"]

    # Copies of the text inside the answer and its prompts are gone; the answer remains.
    after = client.get(f"/v1/answers/{answer_id}").json()
    cited = [e for e in after["evidence"] if e["document_id"] == str(doc_id)]
    assert cited
    assert all(e["deleted"] and e["content"] == "" for e in cited)
    assert after["claims"][0]["quote"] == REDACTED
    assert after["answer"] == before["answer"]
    prompts = owner_query(
        database,
        "SELECT request::text FROM app.llm_calls WHERE run_id = :r AND purpose = 'draft'",
        r=answer_id,
    )
    assert prompts
    assert all("ninety days" not in p[0] for p in prompts)

    audit = owner_query(
        database,
        "SELECT action, target, outcome FROM app.audit_log WHERE target = :t",
        t=f"document:{doc_id}",
    )
    assert [tuple(a) for a in audit] == [("document.delete", f"document:{doc_id}", "success")]

    again = client.delete(f"/v1/documents/{document['id']}", headers={"X-CSRF-Token": csrf})
    assert again.status_code == 404


def test_deleting_a_knowledge_base_removes_all_its_documents(
    writer: tuple[TestClient, str], blob_root: Path
) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    other_kb = new_kb(client, csrf)
    first = upload(client, csrf, kb, "policy.md", POLICY_MD)
    second = upload(client, csrf, kb, "backup.md", SECOND_MD)
    survivor = upload(client, csrf, other_kb, "policy.md", POLICY_MD)
    drain(client)

    response = client.delete(f"/v1/knowledge-bases/{kb}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204

    ids = [UUID(str(first["id"])), UUID(str(second["id"]))]
    assert set(residuals(client, ids).values()) == {0}
    assert [p.name for p in blobs(blob_root)] == [str(survivor["id"])]
    assert kb not in [k["id"] for k in client.get("/v1/knowledge-bases").json()]
    assert client.get(f"/v1/knowledge-bases/{kb}/documents").status_code == 404


def test_deleting_a_document_before_it_is_indexed_cancels_its_job(
    writer: tuple[TestClient, str],
) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    document = upload(client, csrf, kb, "policy.md", POLICY_MD)
    response = client.delete(f"/v1/documents/{document['id']}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert drain(client) == 0
    assert residuals(client, [UUID(str(document["id"]))])["jobs"] == 0


def test_deletion_needs_csrf_and_cannot_cross_tenants(
    writer: tuple[TestClient, str], database: Database
) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    document = upload(client, csrf, kb, "policy.md", POLICY_MD)
    drain(client)  # the test database is shared: leave no queued job for later tests
    assert client.delete(f"/v1/documents/{document['id']}").status_code == 403
    assert (
        client.delete(f"/v1/documents/{uuid.uuid4()}", headers={"X-CSRF-Token": csrf}).status_code
        == 404
    )

    # Another organization's request is filtered by RLS: it looks like the row does not exist.
    other_org = uuid.uuid4()
    engine = create_sync_engine(database.url("agentforge_owner"))
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO app.organizations (id, name, slug) VALUES (:o, 'Other', :s)"),
            {"o": other_org, "s": f"other-{other_org.hex[:8]}"},
        )
    engine.dispose()
    svc = services_of(client)
    from agentforge.knowledge.service import Actor, NotFoundError

    with pytest.raises(NotFoundError):
        client.portal.call(  # type: ignore[union-attr]
            svc.knowledge.delete_document, other_org, UUID(str(document["id"])), Actor(None, None)
        )
    names = [d["filename"] for d in client.get(f"/v1/knowledge-bases/{kb}/documents").json()]
    assert names == ["policy.md"]
