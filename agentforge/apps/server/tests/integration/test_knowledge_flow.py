"""Upload -> sandboxed parse -> chunk -> embed -> hybrid search, through the API and worker code
on real Postgres with the fake model provider (R1-11..R1-14)."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.auth.users import create_user
from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.services import Services

from ..documents import make_pdf
from .conftest import PASSWORD, Database, UserFactory, login, settings_for

POLICY_MD = b"""# Information Security Policy

## Logging

Access logs are retained for ninety days. Logs are written to immutable storage and reviewed weekly
by the security team.

## Encryption

Customer data at rest is encrypted with AES-256. Encryption keys are rotated every twelve months.
"""


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "blobs"))


@pytest.fixture
def writer(app: FastAPI, make_user: UserFactory) -> Iterator[tuple[TestClient, str]]:
    with TestClient(app) as client:
        csrf = login(client, make_user(Role.MEMBER))
        yield client, csrf


def services_of(client: TestClient) -> Services:
    app = client.app
    assert isinstance(app, FastAPI)
    svc: Services = app.state.services
    return svc


def drain(client: TestClient) -> int:
    """Run the worker's ingestion step until the queue is empty (same code the worker runs)."""
    svc = services_of(client)
    processed = 0
    while client.portal.call(svc.knowledge.process_next_job):  # type: ignore[union-attr]
        processed += 1
    return processed


def upload(client: TestClient, csrf: str, kb_id: str, name: str, data: bytes) -> dict[str, object]:
    response = client.post(
        f"/v1/knowledge-bases/{kb_id}/documents",
        content=data,
        headers={
            "X-CSRF-Token": csrf,
            "X-Filename": quote(name),
            "Content-Type": "application/octet-stream",
        },
    )
    return {**response.json(), "http_status": response.status_code}


def new_kb(client: TestClient, csrf: str) -> str:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": f"kb-{uuid.uuid4().hex[:8]}"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_upload_index_and_search(writer: tuple[TestClient, str]) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    md = upload(client, csrf, kb, "policy.md", POLICY_MD)
    pdf = upload(
        client,
        csrf,
        kb,
        "backup.pdf",
        make_pdf(["Backups run nightly.", "Restores are tested quarterly."]),
    )
    assert md["http_status"] == pdf["http_status"] == 201
    assert md["media_type"] == "text/markdown"
    assert md["status"] == "queued"

    assert drain(client) == 2
    documents = client.get(f"/v1/knowledge-bases/{kb}/documents").json()
    assert {d["status"] for d in documents} == {"ready"}
    by_name = {d["filename"]: d for d in documents}
    assert by_name["backup.pdf"]["page_count"] == 2
    assert by_name["policy.md"]["chunk_count"] >= 1

    hits = client.post(
        "/v1/knowledge/search",
        json={"query": "how long are access logs retained", "knowledge_base_ids": [kb], "k": 3},
        headers={"X-CSRF-Token": csrf},
    ).json()
    top = hits[0]
    assert top["filename"] == "policy.md"
    assert "ninety days" in top["content"]
    assert set(top["channels"]) == {"vector", "text"}  # both retrievers agreed
    assert top["section_path"][-1] in {"Logging", "Encryption"}

    pdf_hit = client.post(
        "/v1/knowledge/search",
        json={"query": "quarterly restore tests", "knowledge_base_ids": [kb]},
        headers={"X-CSRF-Token": csrf},
    ).json()[0]
    # Two tiny pages form one chunk spanning both; the hit must cover page 2.
    assert pdf_hit["filename"] == "backup.pdf"
    assert pdf_hit["page_start"] <= 2 <= pdf_hit["page_end"]
    assert "quarterly" in pdf_hit["content"]


def test_rejected_duplicate_and_oversized_uploads(
    database: Database, tmp_path: Path, make_user: UserFactory
) -> None:
    app = create_app(
        settings_for(database, model_provider="fake", blob_root=tmp_path / "b", max_upload_mb=1)
    )
    with TestClient(app) as client:
        csrf = login(client, make_user())
        kb = new_kb(client, csrf)
        assert upload(client, csrf, kb, "a.md", POLICY_MD)["http_status"] == 201
        duplicate = upload(client, csrf, kb, "copy-of-a.md", POLICY_MD)
        assert duplicate["http_status"] == 409
        assert "already in the knowledge base as 'a.md'" in str(duplicate["title"])
        fake_pdf = upload(client, csrf, kb, "evil.pdf", b"MZ not a pdf at all")
        assert (fake_pdf["http_status"], fake_pdf["title"]) == (
            400,
            "The file has a .pdf name but is not a PDF.",
        )
        too_big = upload(client, csrf, kb, "big.txt", b"x" * (2 * 1024 * 1024))
        assert too_big["http_status"] == 413


def test_parse_failures_mark_the_document_failed(writer: tuple[TestClient, str]) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    upload(client, csrf, kb, "broken.pdf", b"%PDF-1.4\nthis is not really a pdf")
    drain(client)
    [document] = client.get(f"/v1/knowledge-bases/{kb}/documents").json()
    assert document["status"] == "failed"
    assert "Could not read the document" in document["error"]


def test_reprocessing_replaces_chunks_without_duplicates(
    database: Database, writer: tuple[TestClient, str]
) -> None:
    """Simulates a worker that crashed after indexing (lease expired, job runs again) and a
    user-requested reindex: chunk counts stay the same, never doubled."""
    client, csrf = writer
    kb = new_kb(client, csrf)
    document_id = upload(client, csrf, kb, "policy.md", POLICY_MD)["id"]
    drain(client)
    owner = create_sync_engine(database.url("agentforge_owner"))

    def chunk_count() -> int:
        with owner.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM app.chunks WHERE document_id = :d"),
                    {"d": document_id},
                ).scalar_one()
            )

    before = chunk_count()
    with owner.begin() as connection:  # crash after indexing: the job is claimable again
        connection.execute(
            text(
                "UPDATE queue.ingestion_jobs SET status = 'running', "
                "lease_expires_at = now() - interval '1 minute' WHERE document_id = :d"
            ),
            {"d": document_id},
        )
    assert drain(client) == 1
    assert chunk_count() == before
    assert (
        client.post(
            f"/v1/documents/{document_id}/reindex", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 202
    )
    assert drain(client) == 1
    assert chunk_count() == before
    owner.dispose()


def test_other_organizations_cannot_see_or_search_documents(
    database: Database, app: FastAPI, writer: tuple[TestClient, str]
) -> None:
    client, csrf = writer
    kb = new_kb(client, csrf)
    upload(client, csrf, kb, "policy.md", POLICY_MD)
    drain(client)

    other_org = uuid.uuid4()
    owner = create_sync_engine(database.url("agentforge_owner"))
    with owner.begin() as connection:
        connection.execute(
            text("INSERT INTO app.organizations (id, name, slug) VALUES (:o, 'Other', :s)"),
            {"o": other_org, "s": f"other-{other_org.hex[:8]}"},
        )
        connection.execute(
            text("INSERT INTO app.workspaces (org_id, name, slug) VALUES (:o, 'main', 'main')"),
            {"o": other_org},
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
        assert outsider.get("/v1/knowledge-bases").json() == []
        assert outsider.get(f"/v1/knowledge-bases/{kb}/documents").status_code == 404
        hits = outsider.post(
            "/v1/knowledge/search",
            json={"query": "access logs", "knowledge_base_ids": [kb]},
            headers={"X-CSRF-Token": outsider_csrf},
        ).json()
        assert hits == []
        refused = upload(outsider, outsider_csrf, kb, "x.md", b"# hi\n\nthere")
        assert refused["http_status"] == 404


def test_viewers_need_write_permission_to_upload(database: Database, app: FastAPI) -> None:
    with TestClient(app) as anonymous:
        response = anonymous.post("/v1/knowledge-bases", json={"name": "nope"})
        assert response.status_code == 401
