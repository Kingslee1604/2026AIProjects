"""Redact deleted documents' text from answers and the model calls that produced them.

Answers keep an evidence snapshot so citations stay viewable after a reindex; when a document
is deleted, that snapshot (and the prompt that carried it) must go too (NFR-13). The answer
text and claims stay: they are the organization's own output, and the audit trail needs them.
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REDACTED = "[removed: the source document was deleted]"


async def purge_documents(db: AsyncSession, document_ids: list[UUID]) -> None:
    if not document_ids:
        return
    ids = [str(d) for d in document_ids]
    rows = (
        await db.execute(
            text(
                "SELECT id, claims, evidence FROM app.qa_answers WHERE EXISTS ("
                "SELECT 1 FROM jsonb_array_elements(evidence) e "
                "WHERE e->>'document_id' = ANY(:ids)) FOR UPDATE"
            ),
            {"ids": ids},
        )
    ).all()
    for row in rows:
        evidence: list[dict[str, Any]] = list(row.evidence)
        removed = {e["id"] for e in evidence if e.get("document_id") in ids}
        for e in evidence:
            if e["id"] in removed:
                e.update(content="", deleted=True)
        claims: list[dict[str, Any]] = list(row.claims)
        for c in claims:
            if removed & set(c.get("citations", [])):
                c["quote"] = REDACTED
        await db.execute(
            text(
                "UPDATE app.qa_answers "
                "SET evidence = CAST(:e AS jsonb), claims = CAST(:c AS jsonb) WHERE id = :i"
            ),
            {"e": json.dumps(evidence), "c": json.dumps(claims), "i": row.id},
        )
    if rows:
        # Prompts sent for these answers contained the evidence text.
        await db.execute(
            text(
                "UPDATE app.llm_calls SET request = CAST(:r AS jsonb), response = NULL "
                "WHERE run_id = ANY(:runs) AND purpose IN ('draft', 'verify')"
            ),
            {
                "r": json.dumps([{"role": "system", "content": REDACTED}]),
                "runs": [r.id for r in rows],
            },
        )
