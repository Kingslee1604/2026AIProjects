"""/v1/runs/{id}/review: read the review, decide items, bulk-approve, complete."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agentforge.api.deps import client_ip, require
from agentforge.api.knowledge_routes import ServicesDep
from agentforge.auth.rbac import Permission, Principal
from agentforge.reviews.service import NotFoundError, ReviewBatch, Reviewer, ReviewError
from agentforge.runs.service import InvalidStateError

router = APIRouter(tags=["reviews"])

ReaderDep = Annotated[Principal, Depends(require(Permission.RUNS_READ))]
ReviewerDep = Annotated[Principal, Depends(require(Permission.REVIEWS_DECIDE))]


class ReviewItemOut(BaseModel):
    item_index: int
    auto_accept_candidate: bool
    decision: Literal["approved", "edited", "rejected"] | None
    edited_answer: str | None
    reason: str | None
    decided_by: UUID | None
    decided_at: datetime | None


class ReviewOut(BaseModel):
    id: UUID
    run_id: UUID
    epoch: int
    status: Literal["open", "completed"]
    items: list[ReviewItemOut]
    undecided: int
    created_at: datetime
    completed_at: datetime | None


class DecisionIn(BaseModel):
    decision: Literal["approved", "edited", "rejected"]
    edited_answer: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=1000)


class BulkOut(BaseModel):
    approved: int


class QueueEntryOut(BaseModel):
    run_id: UUID
    title: str
    epoch: int
    total: int
    undecided: int
    candidates: int
    created_at: datetime


def _out(batch: ReviewBatch) -> ReviewOut:
    data = asdict(batch)
    return ReviewOut(**data, undecided=batch.undecided)


def _reviewer(principal: Principal, request: Request) -> Reviewer:
    return Reviewer(principal.user_id, principal.email, client_ip(request))


@router.get("/v1/reviews", response_model=list[QueueEntryOut])
async def review_queue(principal: ReaderDep, svc: ServicesDep) -> list[QueueEntryOut]:
    """Runs waiting for a review, oldest first, with how many answers still need a decision."""
    return [QueueEntryOut(**asdict(e)) for e in await svc.reviews.queue(principal.org_id)]


@router.get("/v1/runs/{run_id}/review", response_model=ReviewOut)
async def get_review(run_id: UUID, principal: ReaderDep, svc: ServicesDep) -> ReviewOut:
    try:
        return _out(await svc.reviews.get(principal.org_id, run_id))
    except NotFoundError:
        raise HTTPException(404, "Review not found") from None


@router.post("/v1/runs/{run_id}/review/items/{item_index}", response_model=ReviewItemOut)
async def decide_item(
    run_id: UUID,
    item_index: int,
    body: DecisionIn,
    request: Request,
    principal: ReviewerDep,
    svc: ServicesDep,
) -> ReviewItemOut:
    """Approve, edit and approve (`edited_answer` required), or reject (`reason` required)."""
    try:
        item = await svc.reviews.decide(
            principal.org_id,
            run_id,
            item_index,
            body.decision,
            _reviewer(principal, request),
            edited_answer=body.edited_answer,
            reason=body.reason,
        )
    except NotFoundError as error:
        raise HTTPException(404, str(error).capitalize()) from None
    except ReviewError as error:
        raise HTTPException(409 if "complete" in str(error) else 400, str(error)) from None
    return ReviewItemOut(**asdict(item))


@router.post("/v1/runs/{run_id}/review/approve-supported", response_model=BulkOut)
async def approve_supported(
    run_id: UUID, request: Request, principal: ReviewerDep, svc: ServicesDep
) -> BulkOut:
    """Approve every undecided answer that is verified with high confidence."""
    try:
        count = await svc.reviews.approve_supported(
            principal.org_id, run_id, _reviewer(principal, request)
        )
    except NotFoundError:
        raise HTTPException(404, "Review not found") from None
    except ReviewError as error:
        raise HTTPException(409, str(error)) from None
    return BulkOut(approved=count)


@router.post("/v1/runs/{run_id}/review/complete", response_model=ReviewOut)
async def complete_review(
    run_id: UUID, request: Request, principal: ReviewerDep, svc: ServicesDep
) -> ReviewOut:
    """Close the review (every answer needs a decision); the run then exports the file."""
    try:
        batch = await svc.reviews.complete(principal.org_id, run_id, _reviewer(principal, request))
    except NotFoundError:
        raise HTTPException(404, "Review not found") from None
    except (ReviewError, InvalidStateError) as error:
        raise HTTPException(409, str(error)) from None
    return _out(batch)
