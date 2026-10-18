"""`agentforge eval run` end to end (R1-40) on real Postgres with the fake model: the Northwind
corpus is indexed into a temporary knowledge base, retrieval and answers are measured, the
report is written, and the knowledge base is gone afterwards."""

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from agentforge.db.engine import create_sync_engine
from agentforge.db.migrate import DEFAULT_ORG_ID
from agentforge.evaluation.runner import TEMPORARY
from agentforge.evaluation_cli import add_arguments, run
from tools.demo_corpus import BUILD, ROOT, build

from .conftest import Database, settings_for


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return parser.parse_args(["run", *argv])


def _leftovers(database: Database) -> int:
    """Rows of evaluation knowledge bases, their documents, and their chunks still stored."""
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM app.knowledge_bases WHERE name LIKE 'eval-%') "
                    "+ (SELECT count(*) FROM app.documents d JOIN app.knowledge_bases k "
                    "ON k.id = d.knowledge_base_id WHERE k.name LIKE 'eval-%')"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def _leave_a_killed_runs_knowledge_base(database: Database) -> None:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app.knowledge_bases (org_id, workspace_id, name, description) "
                    "SELECT org_id, id, 'eval-northwind-killed', :d FROM app.workspaces "
                    "WHERE org_id = :o ORDER BY created_at LIMIT 1"
                ),
                {"d": TEMPORARY, "o": DEFAULT_ORG_ID},
            )
    finally:
        engine.dispose()


def test_an_evaluation_writes_a_complete_report(database: Database, tmp_path: Path) -> None:
    if not (BUILD / "corpus").is_dir():
        build()
    _leave_a_killed_runs_knowledge_base(database)  # removed before the evaluation starts
    settings = settings_for(database, model_provider="fake", blob_root=tmp_path / "blobs")
    out = tmp_path / "report"
    code = run(
        _args("--data", str(ROOT), "--questions", "demo", "--out", str(out), "--git-sha", "abc123"),
        settings,
    )
    assert code == 0

    report = json.loads((out / "report.json").read_text())
    # Screening flags exactly the two adversarial documents, none of the ten policies.
    screening = report["screening"]
    assert (screening["adversarial_flagged"], screening["adversarial"]) == (2, 2)
    assert (screening["genuine_flagged"], screening["genuine"]) == (0, 10)
    retrieval = report["retrieval"]
    assert retrieval["questions"] == 12
    assert retrieval["answerable"] == 10  # the demo subset has 2 unanswerable questions
    assert set(retrieval["recall_at"]) == {"5", "10", "20"}
    assert all(0 <= v <= 1 for v in retrieval["recall_at"].values())
    assert retrieval["recall_at"]["20"] >= retrieval["recall_at"]["5"]
    assert len(retrieval["gate"]["sweep"]) == 11
    assert retrieval["ann_vs_exact_overlap_at_10"] is not None

    [(model, answers)] = report["answers"].items()
    assert model == settings.chat_model
    summary = answers["summary"]
    assert summary["questions"] == 12
    assert summary["answered"] + summary["abstained"] + summary["failed"] == 12
    assert summary["injection_targets"] >= 1
    assert summary["structured_output"]["calls"] >= 1
    assert {row["id"] for row in answers["per_question"]} == {
        row["id"] for row in retrieval["per_question"]
    }

    manifest = report["manifest"]
    assert manifest["git_sha"] == "abc123"
    assert len(manifest["dataset_sha256"]) == 64
    assert manifest["hardware"]["logical_cpus"]

    markdown = (out / "report.md").read_text()
    for heading in ("## Retrieval", "## Abstention gate", f"## Answers: `{model}`"):
        assert heading in markdown

    # Nothing is left behind: the temporary knowledge base was deleted, and so was the one a
    # killed evaluation had left.
    assert _leftovers(database) == 0
