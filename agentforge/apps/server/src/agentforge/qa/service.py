"""Ask a question: the API stores it and enqueues a job; the worker answers it.

Model calls on CPU take tens of seconds, so answering never runs inside an HTTP request.
The answer id doubles as the `run_id` on every model call, linking app.llm_calls to it.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.agents.budget import BudgetExceededError
from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge import queue
from agentforge.models import ModelError
from agentforge.qa.pipeline import GroundedAnswerer

log = get_logger("agentforge.qa")


class NotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    id: UUID
    question: str
    knowledge_base_ids: list[UUID]
    status: str
    answer: str | None
    confidence: str | None
    reason: str | None
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    agent_versions: dict[str, str]
    error: str | None
    timings: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ModelCall:
    id: UUID
    purpose: str
    model: str
    status: str
    attempt: int
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    error_class: str | None
    request: list[dict[str, Any]]
    response: str | None
    created_at: datetime


_COLUMNS = (
    "id, question, knowledge_base_ids, status, answer, confidence, reason, claims, evidence, "
    "agent_versions, error, timings, created_at, completed_at"
)


def _record(row: Any) -> AnswerRecord:
    return AnswerRecord(
        id=row.id,
        question=row.question,
        knowledge_base_ids=list(row.knowledge_base_ids),
        status=row.status,
        answer=row.answer,
        confidence=row.confidence,
        reason=row.reason,
        claims=list(row.claims),
        evidence=list(row.evidence),
        agent_versions=dict(row.agent_versions),
        error=row.error,
        timings=dict(row.timings),
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


class QAService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], answerer: GroundedAnswerer
    ) -> None:
        self._sessions = sessions
        self._answerer = answerer

    async def ask(
        self, org_id: UUID, user_id: UUID, question: str, knowledge_base_ids: Sequence[UUID]
    ) -> UUID:
        kb_ids = list(dict.fromkeys(knowledge_base_ids))
        answer_id = uuid4()
        async with tenant_session(self._sessions, TenantContext(org_id, user_id=user_id)) as db:
            found = (
                await db.execute(
                    text("SELECT count(*) FROM app.knowledge_bases WHERE id = ANY(:ids)"),
                    {"ids": kb_ids},
                )
            ).scalar_one()
            if found != len(kb_ids):  # RLS hides other tenants' knowledge bases
                raise NotFoundError("knowledge base not found")
            await db.execute(
                text(
                    "INSERT INTO app.qa_answers "
                    "(id, org_id, asked_by, question, knowledge_base_ids) "
                    "VALUES (:i, :o, :u, :q, :k)"
                ),
                {"i": answer_id, "o": org_id, "u": user_id, "q": question.strip(), "k": kb_ids},
            )
            await queue.enqueue(db, org_id, answer_id, queue="answer")
        log.info("question_asked", answer_id=str(answer_id), knowledge_bases=len(kb_ids))
        return answer_id

    async def get(self, org_id: UUID, answer_id: UUID) -> AnswerRecord:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text(f"SELECT {_COLUMNS} FROM app.qa_answers WHERE id = :i"),  # noqa: S608
                    {"i": answer_id},
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("answer not found")
        return _record(row)

    async def calls(self, org_id: UUID, answer_id: UUID) -> list[ModelCall]:
        """Every model call made for an answer (linked by run_id), oldest first."""
        await self.get(org_id, answer_id)  # 404 for unknown or other-tenant answers
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    "SELECT id, purpose, model, status, attempt, latency_ms, prompt_tokens, "
                    "completion_tokens, error_class, request, response, created_at "
                    "FROM app.llm_calls WHERE run_id = :r ORDER BY created_at, attempt"
                ),
                {"r": answer_id},
            )
            return [
                ModelCall(
                    id=r.id,
                    purpose=r.purpose,
                    model=r.model,
                    status=r.status,
                    attempt=r.attempt,
                    latency_ms=r.latency_ms,
                    prompt_tokens=r.prompt_tokens,
                    completion_tokens=r.completion_tokens,
                    error_class=r.error_class,
                    request=list(r.request),
                    response=r.response,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def recent(self, org_id: UUID, limit: int = 20) -> list[AnswerRecord]:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM app.qa_answers "  # noqa: S608
                    "ORDER BY created_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
            return [_record(r) for r in rows]

    async def _set(self, org_id: UUID, answer_id: UUID, **values: Any) -> None:
        jsonb = {"claims", "evidence", "agent_versions", "timings"}
        assignments = ", ".join(
            f"{k} = CAST(:{k} AS jsonb)" if k in jsonb else f"{k} = :{k}" for k in values
        )
        if values.get("status") in {"answered", "insufficient_evidence", "failed"}:
            assignments += ", completed_at = now()"
        params = {k: json.dumps(v) if k in jsonb else v for k, v in values.items()}
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            await db.execute(
                text(f"UPDATE app.qa_answers SET {assignments} WHERE id = :id"),  # noqa: S608
                {**params, "id": answer_id},
            )

    async def process_next_job(self) -> bool:
        """Answer one queued question. Returns False when the queue is empty."""
        job = await queue.claim(self._sessions, "answer")
        if job is None:
            return False
        try:
            row = await self.get(job.org_id, job.subject_id)
        except NotFoundError:  # deleted while queued
            await queue.finish(self._sessions, job, ok=True)
            return True
        await self._set(job.org_id, job.subject_id, status="running", error=None)
        try:
            result = await self._answerer.answer(
                job.org_id, row.question, row.knowledge_base_ids, run_id=job.subject_id
            )
        except BudgetExceededError as error:
            await self._set(job.org_id, job.subject_id, status="failed", error=error.code)
            await queue.finish(self._sessions, job, ok=False)
            log.warning("answer_failed", answer_id=str(job.subject_id), error=str(error))
            return True
        except ModelError as error:
            retrying = error.retryable and await queue.retry_later(self._sessions, job)
            if not retrying and not error.retryable:
                await queue.finish(self._sessions, job, ok=False)
            await self._set(
                job.org_id,
                job.subject_id,
                status="pending" if retrying else "failed",
                error=f"{error.__class__.__name__}: {error}"[:500],
            )
            log.warning(
                "answer_deferred" if retrying else "answer_failed",
                answer_id=str(job.subject_id),
                error=error.__class__.__name__,
            )
            return True
        await self._set(
            job.org_id,
            job.subject_id,
            status=result.status,
            answer=result.answer,
            confidence=result.confidence,
            reason=result.reason,
            claims=result.claims_json(),
            evidence=result.evidence_json(),
            agent_versions=result.agent_versions,
            timings={**result.timings, "budget": result.budget},
        )
        await queue.finish(self._sessions, job, ok=True)
        return True
