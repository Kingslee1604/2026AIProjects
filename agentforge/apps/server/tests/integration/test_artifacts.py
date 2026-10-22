"""Export artifacts (R1-35) on real Postgres: a crash anywhere in the export still leaves exactly
one artifact per (run, epoch) (gate H4), a new epoch is a new artifact, and downloads are
authorized (another organization's file is "not found") and audited."""

import asyncio
import dataclasses
import functools
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine
from agentforge.runs import questionnaire as questionnaire_steps
from agentforge.runs.service import NewItem, RunService
from tools.demo_corpus import ROOT as DEMO_ROOT
from tools.demo_corpus import _write_xlsx  # pyright: ignore[reportPrivateUsage]

from .conftest import Database, UserFactory, login, outsider, settings_for
from .test_knowledge_flow import services_of
from .test_runs import RecordingEngine


class SimulatedCrashError(Exception):
    pass


@dataclasses.dataclass
class Exported:
    client: TestClient
    app: FastAPI
    database: Database
    org_id: UUID
    run_id: UUID
    blob_root: Path

    def export(self, epoch: int = 1) -> str:
        steps = services_of(self.client).questionnaire_steps
        return self.client.portal.call(  # type: ignore[union-attr]
            steps.export, self.org_id, self.run_id, epoch
        )

    def query(self, sql: str, **params: object) -> list[Any]:
        engine = create_sync_engine(self.database.url("agentforge_owner"))
        try:
            with engine.connect() as connection:
                return list(connection.execute(text(sql), params).all())
        finally:
            engine.dispose()

    def artifacts(self) -> list[Any]:
        return self.query(
            "SELECT id, epoch, blob_key FROM app.artifacts WHERE run_id = :r ORDER BY epoch",
            r=self.run_id,
        )

    def export_steps(self) -> list[str]:
        rows = self.query(
            "SELECT status FROM app.step_records WHERE run_id = :r AND node_id = 'export' "
            "ORDER BY started_at",
            r=self.run_id,
        )
        return [r.status for r in rows]


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "b"))


@pytest.fixture
def exported(
    app: FastAPI, database: Database, make_user: UserFactory, tmp_path: Path
) -> Iterator[Exported]:
    """A questionnaire run with every item answered, ready to export (no engine involved)."""
    source = json.loads((DEMO_ROOT / "questionnaire/northwind-questionnaire.json").read_text())
    workbook = tmp_path / "northwind.xlsx"
    _write_xlsx(source["questions"][:5], workbook, source["title"])
    with TestClient(app) as client:
        csrf = login(client, make_user(Role.MEMBER))
        uploaded = client.post(
            "/v1/questionnaires",
            content=workbook.read_bytes(),
            headers={
                "X-CSRF-Token": csrf,
                "X-Filename": quote("northwind.xlsx"),
                "Content-Type": "application/octet-stream",
            },
        ).json()
        svc = services_of(client)
        runs = RunService(svc.sessions, RecordingEngine())
        app.state.services = dataclasses.replace(svc, runs=runs)
        session = client.get("/v1/auth/session").json()
        org_id = UUID(session["org_id"])
        run_id: UUID = client.portal.call(  # type: ignore[union-attr]
            functools.partial(
                runs.create,
                org_id,
                UUID(session["user_id"]),
                template_key="questionnaire",
                title="Export test",
                input={"questionnaire_id": uploaded["id"]},
                items=[
                    NewItem(question=i["question"], source_ref=str(i["row"]))
                    for i in uploaded["items"]
                ],
            )
        )
        owner = create_sync_engine(database.url("agentforge_owner"))
        with owner.begin() as connection:
            connection.execute(
                text("UPDATE app.runs SET status = 'running' WHERE id = :r"), {"r": run_id}
            )
            connection.execute(
                text(
                    "UPDATE app.run_items SET status = 'answered', confidence = 'high', "
                    "answer = 'Answer ' || item_index WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        owner.dispose()
        yield Exported(client, app, database, org_id, run_id, tmp_path / "b")


def test_a_crash_while_writing_the_file_leaves_no_artifact_then_one(
    exported: Exported, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*_: object, **__: object) -> None:
        raise SimulatedCrashError("worker died while writing the workbook")

    with monkeypatch.context() as patch:
        patch.setattr(questionnaire_steps, "export_xlsx", crash)
        with pytest.raises(SimulatedCrashError):
            exported.export()
    assert exported.artifacts() == []
    artifact_id = exported.export()  # the engine retries the step
    assert [str(a.id) for a in exported.artifacts()] == [artifact_id]
    assert exported.export_steps() == ["failed", "succeeded"]


def test_a_crash_after_storing_the_file_still_gives_one_artifact(
    exported: Exported, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = services_of(exported.client).blobs
    store = blobs.put

    def store_then_crash(key: str, source: Path) -> None:
        store(key, source)
        raise SimulatedCrashError("worker died before recording the artifact")

    with monkeypatch.context() as patch:
        patch.setattr(blobs, "put", store_then_crash)
        with pytest.raises(SimulatedCrashError):
            exported.export()
    assert exported.artifacts() == []  # the file landed, but no artifact row
    artifact_id = exported.export()
    [artifact] = exported.artifacts()
    assert str(artifact.id) == artifact_id
    assert blobs.exists(artifact.blob_key)  # the retry overwrote the same key, no orphan
    files = list((exported.blob_root / f"org/{exported.org_id}/runs/{exported.run_id}").iterdir())
    assert [f.name for f in files] == ["export-1.xlsx"]


def test_a_crash_after_commit_and_concurrent_retries_reuse_the_artifact(
    exported: Exported,
) -> None:
    first = exported.export()
    assert exported.export() == first  # step re-run after commit (completion not recorded)
    steps = services_of(exported.client).questionnaire_steps

    async def overlap() -> list[str]:  # e.g. a recovered worker racing the original
        return list(
            await asyncio.gather(*(steps.export(exported.org_id, exported.run_id, 2) for _ in "ab"))
        )

    second = exported.client.portal.call(overlap)  # type: ignore[union-attr]
    assert second[0] == second[1] != first
    assert [a.epoch for a in exported.artifacts()] == [1, 2]  # a new epoch is a new artifact


def test_download_is_authorized_and_audited(exported: Exported) -> None:
    client, run = exported.client, exported.run_id
    first = exported.export()
    second = exported.export(epoch=2)
    listed = client.get(f"/v1/runs/{run}/artifacts").json()
    assert [a["id"] for a in listed] == [second, first]  # newest version first
    assert [a["filename"] for a in listed] == [
        "northwind - answered (v2).xlsx",
        "northwind - answered.xlsx",
    ]
    assert "blob_key" not in listed[0]  # storage paths never leave the server

    response = client.get(f"/v1/runs/{run}/artifacts/{first}/download")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "answered.xlsx" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet is not None
    assert "Answer 0" in [c.value for c in sheet[2]]

    assert client.get(f"/v1/runs/{run}/artifacts/{uuid4()}/download").status_code == 404
    assert client.get(f"/v1/runs/{uuid4()}/artifacts/{first}/download").status_code == 404
    audited = exported.query(
        "SELECT target FROM app.audit_log WHERE action = 'artifact.download' AND target LIKE :t",
        t=f"run:{run}%",
    )
    assert [a.target for a in audited] == [f"run:{run}#artifact:{first}"]

    with outsider(exported.app, exported.database) as (other, _):
        assert other.get(f"/v1/runs/{run}/artifacts").status_code == 404
        assert other.get(f"/v1/runs/{run}/artifacts/{first}/download").status_code == 404
