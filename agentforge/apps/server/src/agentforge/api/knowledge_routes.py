"""/v1/knowledge-bases and /v1/knowledge/search."""

import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agentforge.api.deps import client_ip, require
from agentforge.auth.rbac import Permission, Principal
from agentforge.knowledge.retrieval import SearchHit
from agentforge.knowledge.service import (
    Actor,
    DuplicateDocumentError,
    DuplicateNameError,
    NotFoundError,
)
from agentforge.knowledge.validation import UploadRejectedError
from agentforge.services import Services

router = APIRouter(tags=["knowledge"])

Reader = Annotated[Principal, Depends(require(Permission.KB_READ))]
Writer = Annotated[Principal, Depends(require(Permission.KB_WRITE))]


def services(request: Request) -> Services:
    found: Services | None = request.app.state.services
    if found is None:
        raise HTTPException(503, "Database is not configured")
    return found


ServicesDep = Annotated[Services, Depends(services)]


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class KnowledgeBaseOut(BaseModel):
    id: UUID
    name: str
    description: str
    document_count: int
    ready_count: int
    created_at: datetime


class DocumentOut(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    status: str
    error: str | None
    page_count: int | None
    chunk_count: int | None
    created_at: datetime
    updated_at: datetime


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_ids: list[UUID] = Field(min_length=1, max_length=20)
    k: int = Field(default=8, ge=1, le=30)


class SearchHitOut(BaseModel):
    chunk_id: UUID
    document_id: UUID
    filename: str
    content: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: list[str]
    score: float
    vector_rank: int | None
    text_rank: int | None
    vector_distance: float | None
    text_score: float | None
    channels: list[str]


@router.get("/v1/knowledge-bases", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(principal: Reader, svc: ServicesDep) -> list[KnowledgeBaseOut]:
    return [KnowledgeBaseOut(**asdict(kb)) for kb in await svc.knowledge.list_kbs(principal.org_id)]


@router.post("/v1/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201)
async def create_knowledge_base(
    body: KnowledgeBaseIn, principal: Writer, svc: ServicesDep
) -> KnowledgeBaseOut:
    try:
        kb_id = await svc.knowledge.create_kb(
            principal.org_id, principal.user_id, body.name, body.description
        )
    except DuplicateNameError as error:
        raise HTTPException(409, str(error)) from None
    created = next(kb for kb in await svc.knowledge.list_kbs(principal.org_id) if kb.id == kb_id)
    return KnowledgeBaseOut(**asdict(created))


@router.get("/v1/knowledge-bases/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(kb_id: UUID, principal: Reader, svc: ServicesDep) -> list[DocumentOut]:
    try:
        documents = await svc.knowledge.list_documents(principal.org_id, kb_id)
    except NotFoundError:
        raise HTTPException(404, "Knowledge base not found") from None
    return [DocumentOut(**asdict(d)) for d in documents]


@router.post(
    "/v1/knowledge-bases/{kb_id}/documents",
    response_model=DocumentOut,
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
async def upload_document(
    kb_id: UUID,
    request: Request,
    principal: Writer,
    svc: ServicesDep,
    x_filename: Annotated[str, Header(description="URL-encoded file name")],
) -> DocumentOut:
    """Upload one file as the raw request body (streamed to disk after authentication)."""
    filename = unquote(x_filename)
    with tempfile.NamedTemporaryFile(prefix="upload-", delete=True) as tmp:
        size = 0
        async for part in request.stream():
            size += len(part)
            tmp.write(part)
        tmp.flush()
        try:
            document = await svc.knowledge.add_document(
                principal.org_id, kb_id, principal.user_id, Path(tmp.name), filename, size
            )
        except UploadRejectedError as error:
            raise HTTPException(400, str(error)) from None
        except DuplicateDocumentError as error:
            raise HTTPException(409, str(error)) from None
        except NotFoundError:
            raise HTTPException(404, "Knowledge base not found") from None
    return DocumentOut(**asdict(document))


@router.post("/v1/documents/{document_id}/reindex", status_code=202)
async def reindex_document(document_id: UUID, principal: Writer, svc: ServicesDep) -> Response:
    try:
        await svc.knowledge.reindex(principal.org_id, document_id)
    except NotFoundError:
        raise HTTPException(404, "Document not found") from None
    return Response(status_code=202)


def _actor(principal: Principal, request: Request) -> Actor:
    return Actor(principal.user_id, principal.email, client_ip(request))


@router.delete("/v1/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID, request: Request, principal: Writer, svc: ServicesDep
) -> Response:
    """Permanently delete a document: its chunks, vectors, file, and copies in answers."""
    try:
        await svc.knowledge.delete_document(
            principal.org_id, document_id, _actor(principal, request)
        )
    except NotFoundError:
        raise HTTPException(404, "Document not found") from None
    return Response(status_code=204)


@router.delete("/v1/knowledge-bases/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: UUID, request: Request, principal: Writer, svc: ServicesDep
) -> Response:
    """Permanently delete a knowledge base and every document in it."""
    try:
        await svc.knowledge.delete_kb(principal.org_id, kb_id, _actor(principal, request))
    except NotFoundError:
        raise HTTPException(404, "Knowledge base not found") from None
    return Response(status_code=204)


@router.post("/v1/knowledge/search", response_model=list[SearchHitOut])
async def search(body: SearchIn, principal: Reader, svc: ServicesDep) -> list[SearchHitOut]:
    hits: list[SearchHit] = await svc.retriever.search(
        principal.org_id, body.knowledge_base_ids, body.query, k=body.k
    )
    return [SearchHitOut(**asdict(h)) for h in hits]
