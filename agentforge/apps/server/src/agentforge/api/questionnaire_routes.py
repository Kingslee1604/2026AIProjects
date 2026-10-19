"""/v1/questionnaires: upload an XLSX/CSV questionnaire, review the detected columns, fix them."""

import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from openpyxl.utils import column_index_from_string
from pydantic import BaseModel, Field

from agentforge.api.deps import require, require_limited
from agentforge.api.knowledge_routes import ServicesDep
from agentforge.auth.rbac import Permission, Principal
from agentforge.knowledge.validation import UploadRejectedError
from agentforge.questionnaires.importer import ColumnMapping
from agentforge.questionnaires.service import NotFoundError

router = APIRouter(tags=["questionnaires"])

Reader = Annotated[Principal, Depends(require(Permission.RUNS_READ))]
Writer = Annotated[Principal, Depends(require(Permission.RUNS_WRITE))]
Uploader = Annotated[Principal, Depends(require_limited(Permission.RUNS_WRITE, "uploads"))]

COLUMN = r"^[A-Za-z]{1,3}$"


class QuestionnaireItemOut(BaseModel):
    row: int
    question: str
    external_id: str | None
    section: str | None


class QuestionnaireOut(BaseModel):
    id: UUID
    filename: str
    format: str
    sheet: str | None
    header_row: int
    columns: dict[str, Any]
    headers: list[str]
    items: list[QuestionnaireItemOut]
    item_count: int
    warnings: list[str]
    created_at: datetime


class ColumnsIn(BaseModel):
    """Spreadsheet column letters (A, B, ... AA). Answer omitted: add a new column on export."""

    question: str = Field(pattern=COLUMN)
    id: str | None = Field(default=None, pattern=COLUMN)
    section: str | None = Field(default=None, pattern=COLUMN)
    answer: str | None = Field(default=None, pattern=COLUMN)

    def mapping(self) -> ColumnMapping:
        def index(letter: str | None) -> int | None:
            return None if letter is None else column_index_from_string(letter.upper()) - 1

        question = index(self.question)
        assert question is not None  # noqa: S101 - required field
        return ColumnMapping(
            question=question,
            id=index(self.id),
            section=index(self.section),
            answer=index(self.answer),
        )


def _out(record: Any) -> QuestionnaireOut:
    data = asdict(record)
    data.pop("media_type")
    data.pop("size_bytes")
    return QuestionnaireOut(**data)


@router.post(
    "/v1/questionnaires",
    response_model=QuestionnaireOut,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_questionnaire(
    request: Request,
    principal: Uploader,
    svc: ServicesDep,
    x_filename: Annotated[str, Header(description="URL-encoded file name")],
) -> QuestionnaireOut:
    """Upload an Excel (.xlsx) or CSV questionnaire as the raw request body. The response shows
    the detected header row and columns and every question found."""
    filename = unquote(x_filename)
    with tempfile.NamedTemporaryFile(prefix="questionnaire-", delete=True) as tmp:
        size = 0
        async for part in request.stream():
            size += len(part)
            tmp.write(part)
        tmp.flush()
        try:
            record = await svc.questionnaires.upload(
                principal.org_id, principal.user_id, Path(tmp.name), filename, size
            )
        except UploadRejectedError as error:
            raise HTTPException(400, str(error)) from None
    return _out(record)


@router.get("/v1/questionnaires/{questionnaire_id}", response_model=QuestionnaireOut)
async def get_questionnaire(
    questionnaire_id: UUID, principal: Reader, svc: ServicesDep
) -> QuestionnaireOut:
    try:
        return _out(await svc.questionnaires.get(principal.org_id, questionnaire_id))
    except NotFoundError:
        raise HTTPException(404, "Questionnaire not found") from None


@router.put("/v1/questionnaires/{questionnaire_id}/columns", response_model=QuestionnaireOut)
async def set_columns(
    questionnaire_id: UUID, body: ColumnsIn, principal: Writer, svc: ServicesDep
) -> QuestionnaireOut:
    """Choose the columns yourself when the detected ones are wrong."""
    try:
        record = await svc.questionnaires.remap(principal.org_id, questionnaire_id, body.mapping())
    except NotFoundError:
        raise HTTPException(404, "Questionnaire not found") from None
    except UploadRejectedError as error:
        raise HTTPException(400, str(error)) from None
    return _out(record)
