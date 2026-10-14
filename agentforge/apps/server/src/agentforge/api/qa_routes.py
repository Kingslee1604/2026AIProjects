"""/v1/answers: ask a question of one or more knowledge bases; poll for the verified answer."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from agentforge.api.deps import require
from agentforge.api.knowledge_routes import ServicesDep
from agentforge.auth.rbac import Permission, Principal
from agentforge.qa.service import NotFoundError

router = APIRouter(tags=["answers"])

Asker = Annotated[Principal, Depends(require(Permission.RUNS_WRITE))]
Reader = Annotated[Principal, Depends(require(Permission.RUNS_READ))]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    knowledge_base_ids: list[UUID] = Field(min_length=1, max_length=20)


class AskOut(BaseModel):
    id: UUID
    status: str


class ClaimOut(BaseModel):
    text: str
    citations: list[str]
    quote: str
    citations_valid: bool
    quote_found: bool
    # Defaults keep answers stored before these checks existed (2026-10-13) readable.
    quote_supports_claim: bool = False
    drafted_text: str = ""
    verdict: str | None = None
    reason: str | None = None
    status: Literal["verified", "rescued", "partial", "dropped"]
    kept: bool

    @model_validator(mode="before")
    @classmethod
    def _drafted_defaults_to_text(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        fields = cast(dict[str, object], data)
        if "drafted_text" in fields:
            return fields
        return {**fields, "drafted_text": fields.get("text", "")}


class EvidenceOut(BaseModel):
    id: str
    chunk_id: UUID
    document_id: UUID
    filename: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: list[str]
    content: str
    score: float
    vector_distance: float | None
    deleted: bool = False  # the source document was deleted; content removed


class AnswerOut(BaseModel):
    id: UUID
    question: str
    knowledge_base_ids: list[UUID]
    status: Literal["pending", "running", "answered", "insufficient_evidence", "failed"]
    answer: str | None
    confidence: Literal["high", "medium", "low"] | None
    reason: str | None
    claims: list[ClaimOut]
    evidence: list[EvidenceOut]
    agent_versions: dict[str, str]
    error: str | None
    timings: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class ModelCallOut(BaseModel):
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


@router.post("/v1/answers", response_model=AskOut, status_code=202)
async def ask(body: AskIn, principal: Asker, svc: ServicesDep) -> AskOut:
    """Queue a question. Poll GET /v1/answers/{id} until the status is final."""
    try:
        answer_id = await svc.qa.ask(
            principal.org_id, principal.user_id, body.question, body.knowledge_base_ids
        )
    except NotFoundError:
        raise HTTPException(404, "Knowledge base not found") from None
    return AskOut(id=answer_id, status="pending")


@router.get("/v1/answers", response_model=list[AnswerOut])
async def recent_answers(
    principal: Reader, svc: ServicesDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[AnswerOut]:
    return [AnswerOut(**asdict(a)) for a in await svc.qa.recent(principal.org_id, limit)]


@router.get("/v1/answers/{answer_id}", response_model=AnswerOut)
async def get_answer(answer_id: UUID, principal: Reader, svc: ServicesDep) -> AnswerOut:
    try:
        record = await svc.qa.get(principal.org_id, answer_id)
    except NotFoundError:
        raise HTTPException(404, "Answer not found") from None
    return AnswerOut(**asdict(record))


@router.get("/v1/answers/{answer_id}/calls", response_model=list[ModelCallOut])
async def answer_calls(answer_id: UUID, principal: Reader, svc: ServicesDep) -> list[ModelCallOut]:
    """The model calls behind an answer: prompts, responses, tokens, latency, outcome."""
    try:
        calls = await svc.qa.calls(principal.org_id, answer_id)
    except NotFoundError:
        raise HTTPException(404, "Answer not found") from None
    return [ModelCallOut(**asdict(c)) for c in calls]
