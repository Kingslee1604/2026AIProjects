"""Per-caller rate limits on uploads, questions, and runs (security baseline S8): 429 with
Retry-After, per person, each kind counted separately. Login and /mcp limits are tested in
test_auth.py and test_mcp.py."""

import dataclasses
from pathlib import Path

from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.runs.service import RunService

from .conftest import Database, UserFactory, login, settings_for
from .test_knowledge_flow import POLICY_MD, new_kb, services_of
from .test_questionnaires_api import upload as upload_questionnaire
from .test_runs import RecordingEngine

QUESTIONNAIRE = b"Question\nHow long are access logs retained?\n"


def post_document(client: TestClient, csrf: str, kb: str, name: str) -> int:
    return client.post(
        f"/v1/knowledge-bases/{kb}/documents",
        content=POLICY_MD,
        headers={
            "X-CSRF-Token": csrf,
            "X-Filename": name,
            "Content-Type": "application/octet-stream",
        },
    ).status_code


def test_uploads_questions_and_runs_are_limited_per_person(
    database: Database, tmp_path: Path, make_user: UserFactory
) -> None:
    app = create_app(
        settings_for(
            database,
            model_provider="fake",
            blob_root=tmp_path / "b",
            rate_uploads_per_minute=2,
            rate_answers_per_minute=1,
            rate_runs_per_minute=1,
        )
    )
    with TestClient(app) as client, TestClient(app) as other:
        svc = services_of(client)
        engine = RecordingEngine()
        app.state.services = dataclasses.replace(
            svc, runs=RunService(svc.sessions, engine), run_engine=engine
        )
        csrf = login(client, make_user(Role.MEMBER))
        kb = new_kb(client, csrf)

        # Uploads: documents and questionnaires share one limit.
        assert post_document(client, csrf, kb, "a.md") == 201
        status, questionnaire = upload_questionnaire(client, csrf, "q.csv", QUESTIONNAIRE)
        assert status == 201
        refused = client.post(
            f"/v1/knowledge-bases/{kb}/documents",
            content=POLICY_MD,
            headers={
                "X-CSRF-Token": csrf,
                "X-Filename": "c.md",
                "Content-Type": "application/octet-stream",
            },
        )
        assert refused.status_code == 429
        assert 1 <= int(refused.headers["Retry-After"]) <= 61
        assert "Too many uploads" in refused.json()["title"]

        # Questions.
        ask = {"question": "How long are access logs retained?", "knowledge_base_ids": [kb]}
        headers = {"X-CSRF-Token": csrf}
        assert client.post("/v1/answers", json=ask, headers=headers).status_code == 202
        assert client.post("/v1/answers", json=ask, headers=headers).status_code == 429

        # Runs.
        start = {"questionnaire_id": questionnaire["id"], "knowledge_base_ids": [kb]}
        assert client.post("/v1/runs", json=start, headers=headers).status_code == 201
        assert client.post("/v1/runs", json=start, headers=headers).status_code == 429

        # Another person is not affected.
        other_csrf = login(other, make_user(Role.MEMBER))
        other_kb = new_kb(other, other_csrf)
        assert post_document(other, other_csrf, other_kb, "a.md") == 201
