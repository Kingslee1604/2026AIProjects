"""No secret reaches the logs (security baseline S11), even at DEBUG.

Signs in (with a wrong password first), creates and uses an API key, presents a bad key, and
uploads a document; then scans every log line for the password, the key, the session cookie,
the CSRF token, and the database password."""

import io
from pathlib import Path

from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.core.logging import configure_logging

from .conftest import PASSWORD, Database, UserFactory, login, settings_for
from .test_knowledge_flow import POLICY_MD, drain, new_kb, upload


def test_no_secret_reaches_the_logs(
    database: Database, tmp_path: Path, make_user: UserFactory
) -> None:
    app = create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "b"))
    logs = io.StringIO()
    configure_logging("DEBUG", "json", stream=logs)
    try:
        with TestClient(app) as client:
            email = make_user(Role.ADMIN)
            wrong = client.post("/v1/auth/login", json={"email": email, "password": f"x{PASSWORD}"})
            assert wrong.status_code == 401
            csrf = login(client, email)
            cookie = client.cookies.get("af_session")
            assert cookie
            created = client.post(
                "/v1/api-keys",
                json={"name": "log test", "scopes": ["api:read"]},
                headers={"X-CSRF-Token": csrf},
            )
            key = str(created.json()["key"])
            bearer = {"Authorization": f"Bearer {key}"}
            assert client.get("/v1/knowledge-bases", headers=bearer).status_code == 200
            bad = {"Authorization": "Bearer af_nosuchkey1_" + "y" * 43}
            assert client.get("/v1/knowledge-bases", headers=bad).status_code == 401
            kb = new_kb(client, csrf)
            upload(client, csrf, kb, "policy.md", POLICY_MD)
            assert drain(client) >= 1  # indexing logs too (other tests' jobs may be queued)
    finally:
        configure_logging("INFO", "json")
    text = logs.getvalue()
    assert '"event": "http_request"' in text  # the logs were captured
    secrets = {
        "password": PASSWORD,
        "wrong password": f"x{PASSWORD}",
        "API key": key,
        "API key secret": key.split("_", 2)[2],  # af_<prefix>_<secret>; the secret may hold "_"
        "presented bad key": "y" * 43,
        "session cookie": cookie,
        "CSRF token": csrf,
        "database password": database.passwords["agentforge_app"],
    }
    leaked = [name for name, value in secrets.items() if value in text]
    assert leaked == []
