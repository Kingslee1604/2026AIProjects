"""/v1/runs: list and read runs, their items and events, cancel, and follow them live (SSE)."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentforge.api.deps import require
from agentforge.api.knowledge_routes import ServicesDep
from agentforge.auth.rbac import Permission, Principal
from agentforge.questionnaires.service import NotFoundError as QuestionnaireNotFoundError
from agentforge.runs.events import EventHub, RunEvent
from agentforge.runs.service import FINAL_STATUSES, InvalidStateError, NewItem, NotFoundError

router = APIRouter(tags=["runs"])

Reader = Annotated[Principal, Depends(require(Permission.RUNS_READ))]
Writer = Annotated[Principal, Depends(require(Permission.RUNS_WRITE))]


class StartRunIn(BaseModel):
    questionnaire_id: UUID
    knowledge_base_ids: list[UUID] = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=300)


class EpochOut(BaseModel):
    epoch: int


RunStatus = Literal[
    "queued", "running", "waiting_review", "succeeded", "failed", "cancelled", "timed_out"
]
KEEPALIVE_SECONDS = 15.0
RECHECK_SECONDS = 3.0  # streams re-read even without a notification (a lost NOTIFY only delays)


class RunOut(BaseModel):
    id: UUID
    template_key: str
    title: str
    status: RunStatus
    input: dict[str, Any]
    budgets: dict[str, Any]
    usage: dict[str, Any]
    epoch: int
    error_class: str | None
    error: str | None
    item_count: int
    items_done: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunItemOut(BaseModel):
    id: UUID
    item_index: int
    source_ref: str | None
    section: str | None
    question: str
    status: str
    answer: str | None
    confidence: str | None
    reason: str | None
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    completed_at: datetime | None


class RunEventOut(BaseModel):
    seq: int
    run_id: UUID
    type: str
    node_id: str | None
    item_index: int | None
    epoch: int | None
    step_id: UUID | None
    data: dict[str, Any]
    created_at: datetime


def _event_out(event: RunEvent) -> RunEventOut:
    return RunEventOut(**asdict(event))


@router.get("/v1/runs", response_model=list[RunOut])
async def list_runs(
    principal: Reader,
    svc: ServicesDep,
    status: RunStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    runs = await svc.runs.list_runs(principal.org_id, limit=limit, status=status)
    return [RunOut(**asdict(r)) for r in runs]


@router.get("/v1/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID, principal: Reader, svc: ServicesDep) -> RunOut:
    try:
        return RunOut(**asdict(await svc.runs.get(principal.org_id, run_id)))
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None


@router.get("/v1/runs/{run_id}/items", response_model=list[RunItemOut])
async def run_items(run_id: UUID, principal: Reader, svc: ServicesDep) -> list[RunItemOut]:
    try:
        items = await svc.runs.items(principal.org_id, run_id)
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None
    return [RunItemOut(**asdict(i)) for i in items]


@router.get("/v1/runs/{run_id}/events", response_model=list[RunEventOut])
async def run_events(
    run_id: UUID,
    principal: Reader,
    svc: ServicesDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[RunEventOut]:
    try:
        events = await svc.runs.events(principal.org_id, run_id, after)
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None
    return [_event_out(e) for e in events]


@router.post("/v1/runs", response_model=RunOut, status_code=201)
async def start_run(body: StartRunIn, principal: Writer, svc: ServicesDep) -> RunOut:
    """Start answering an uploaded questionnaire from the chosen knowledge bases."""
    try:
        questionnaire = await svc.questionnaires.get(principal.org_id, body.questionnaire_id)
    except QuestionnaireNotFoundError:
        raise HTTPException(404, "Questionnaire not found") from None
    items = [
        NewItem(question=i.question, section=i.section, source_ref=str(i.row))
        for i in questionnaire.parsed_items()
    ]
    try:
        run_id = await svc.runs.start_questionnaire(
            principal.org_id,
            principal.user_id,
            questionnaire_id=questionnaire.id,
            items=items,
            knowledge_base_ids=body.knowledge_base_ids,
            title=body.title or questionnaire.filename,
        )
    except NotFoundError:
        raise HTTPException(404, "Knowledge base not found") from None
    return RunOut(**asdict(await svc.runs.get(principal.org_id, run_id)))


@router.post("/v1/runs/{run_id}/items/{item_index}/retry", response_model=EpochOut, status_code=202)
async def retry_item(
    run_id: UUID, item_index: int, principal: Writer, svc: ServicesDep
) -> EpochOut:
    """Answer one item again (only while the run waits for review)."""
    try:
        return EpochOut(epoch=await svc.runs.retry_item(principal.org_id, run_id, item_index))
    except NotFoundError as error:
        raise HTTPException(404, str(error).capitalize()) from None
    except InvalidStateError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/v1/runs/{run_id}/export/retry", response_model=EpochOut, status_code=202)
async def retry_export(run_id: UUID, principal: Writer, svc: ServicesDep) -> EpochOut:
    """Export again: a new version of the answered file (the earlier one is kept)."""
    try:
        return EpochOut(epoch=await svc.runs.retry_export(principal.org_id, run_id))
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None
    except InvalidStateError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/v1/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: UUID, principal: Writer, svc: ServicesDep) -> RunOut:
    try:
        run = await svc.runs.cancel(principal.org_id, run_id, principal.user_id)
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None
    except InvalidStateError as error:
        raise HTTPException(409, str(error)) from None
    return RunOut(**asdict(run))


def _sse(event: RunEvent) -> str:
    payload = _event_out(event).model_dump_json()
    return f"id: {event.seq}\nevent: run_event\ndata: {payload}\n\n"


@router.get(
    "/v1/runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_run(
    run_id: UUID,
    request: Request,
    principal: Reader,
    svc: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """Server-sent events for one run. Reconnect with `Last-Event-ID` (the browser does this
    automatically) to continue after the last event received: no gaps, no duplicates. The
    stream ends with an `end` event once the run is final and every event has been sent."""
    try:
        await svc.runs.get(principal.org_id, run_id)
    except NotFoundError:
        raise HTTPException(404, "Run not found") from None
    start = after
    if last_event_id and last_event_id.isdigit():
        start = max(start, int(last_event_id))
    hub: EventHub | None = request.app.state.event_hub
    org_id = principal.org_id

    async def body() -> AsyncIterator[str]:
        last = start
        idle = 0.0
        with hub.subscribe(run_id) if hub else contextlib.nullcontext(asyncio.Event()) as wake:
            yield "retry: 2000\n\n"
            while True:
                wake.clear()
                events = await svc.runs.events(org_id, run_id, last)
                for event in events:
                    last = event.seq
                    yield _sse(event)
                if events:
                    idle = 0.0
                    continue  # there may be more than one page
                status = (await svc.runs.get(org_id, run_id)).status
                if status in FINAL_STATUSES:
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                if await request.is_disconnected():  # closed tab: stop instead of idling
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), RECHECK_SECONDS)
                idle += RECHECK_SECONDS
                if idle >= KEEPALIVE_SECONDS:
                    idle = 0.0
                    yield ": keep-alive\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
