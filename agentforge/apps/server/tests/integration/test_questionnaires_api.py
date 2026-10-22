"""Questionnaire upload API (R1-31) on real Postgres: upload, read, re-map, limits, tenants."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from tools.demo_corpus import ROOT as DEMO_ROOT
from tools.demo_corpus import _write_xlsx  # pyright: ignore[reportPrivateUsage]

from .conftest import Database, UserFactory, login, outsider, settings_for


@pytest.fixture
def northwind(tmp_path: Path) -> bytes:
    source = json.loads((DEMO_ROOT / "questionnaire/northwind-questionnaire.json").read_text())
    path = tmp_path / "northwind.xlsx"
    _write_xlsx(source["questions"], path, source["title"])
    return path.read_bytes()


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(
        settings_for(database, model_provider="fake", blob_root=tmp_path / "blobs", max_upload_mb=1)
    )


@pytest.fixture
def member(app: FastAPI, make_user: UserFactory) -> Iterator[tuple[TestClient, str]]:
    with TestClient(app) as client:
        yield client, login(client, make_user(Role.MEMBER))


def upload(client: TestClient, csrf: str, name: str, data: bytes) -> tuple[int, dict[str, Any]]:
    response = client.post(
        "/v1/questionnaires",
        content=data,
        headers={
            "X-CSRF-Token": csrf,
            "X-Filename": quote(name),
            "Content-Type": "application/octet-stream",
        },
    )
    return response.status_code, response.json()


def test_upload_read_and_remap(
    member: tuple[TestClient, str], northwind: bytes, tmp_path: Path
) -> None:
    client, csrf = member
    status, body = upload(client, csrf, "Northwind questionnaire.xlsx", northwind)
    assert status == 201, body
    assert body["item_count"] == 40
    assert body["columns"]["letters"] == {"question": "C", "id": "A", "section": "B", "answer": "D"}
    assert body["headers"][:3] == ["ID", "Section", "Question"]
    questionnaire_id = body["id"]

    again = client.get(f"/v1/questionnaires/{questionnaire_id}").json()
    assert again["items"] == body["items"]

    remapped = client.put(
        f"/v1/questionnaires/{questionnaire_id}/columns",
        json={"question": "C", "id": "A", "answer": "E"},  # section dropped, answers into E
        headers={"X-CSRF-Token": csrf},
    )
    assert remapped.status_code == 200, remapped.text
    assert remapped.json()["columns"]["letters"]["answer"] == "E"
    assert remapped.json()["items"][0]["section"] is None
    bad = client.put(
        f"/v1/questionnaires/{questionnaire_id}/columns",
        json={"question": "1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422


def test_rejections(member: tuple[TestClient, str]) -> None:
    client, csrf = member
    status, body = upload(client, csrf, "notes.docx", b"PK\x03\x04whatever")
    assert status == 400
    assert "Excel" in str(body["title"])
    status, body = upload(client, csrf, "q.csv", b"Name,Email\nA,b@example.test\n")
    assert status == 400
    assert "question column" in str(body["title"])
    status, _ = upload(client, csrf, "big.csv", b"Question\n" + b"x" * (2 * 1024 * 1024))
    assert status == 413  # rejected while streaming, before parsing


def test_other_organizations_get_404(
    member: tuple[TestClient, str], northwind: bytes, database: Database, app: FastAPI
) -> None:
    client, csrf = member
    _, body = upload(client, csrf, "northwind.xlsx", northwind)
    path = f"/v1/questionnaires/{body['id']}"
    with outsider(app, database) as (other, other_csrf):
        assert other.get(path).status_code == 404
        remap = other.put(
            f"{path}/columns", json={"question": "C"}, headers={"X-CSRF-Token": other_csrf}
        )
        assert remap.status_code == 404
    with TestClient(app) as anonymous:
        assert anonymous.get(path).status_code == 401
    assert client.get(path).status_code == 200
