"""/v1/overview: the dashboard's numbers for the caller's organization."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentforge.api.deps import require
from agentforge.api.knowledge_routes import ServicesDep
from agentforge.auth.rbac import Permission, Principal

router = APIRouter(tags=["overview"])

Reader = Annotated[Principal, Depends(require(Permission.RUNS_READ))]


class ModelStatusOut(BaseModel):
    provider: str
    reachable: bool
    chat_model: str
    chat_model_installed: bool
    embedding_model: str
    embedding_model_installed: bool


class ActivityOut(BaseModel):
    run_id: UUID
    title: str
    type: str
    at: datetime


class OverviewOut(BaseModel):
    runs_by_status: dict[str, int]
    answers_to_review: int = Field(description="Undecided answers in open reviews.")
    reviews_open: int
    pipeline: dict[str, int] = Field(
        description="Questions of queued or running runs, by status (pending, answering, ...)."
    )
    sentences_30d: dict[str, int] = Field(
        description="Kept sentences of the last 30 days by proof: verified (exact quote), "
        "rescued (AI check), partial."
    )
    questions_7d: dict[str, int]
    model_calls_7d: int
    model_timeouts_7d: int
    tokens_7d: int
    call_seconds_p50_7d: float | None
    knowledge_bases: int
    documents_by_status: dict[str, int]
    documents_flagged: int
    activity: list[ActivityOut]
    model: ModelStatusOut | None


@router.get("/v1/overview", response_model=OverviewOut)
async def overview(principal: Reader, svc: ServicesDep) -> OverviewOut:
    """Counts for the dashboard: runs, reviews, verification, model use, documents, activity."""
    return OverviewOut(**asdict(await svc.overview.get(principal.org_id)))
