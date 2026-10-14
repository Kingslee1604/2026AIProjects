"""Knowledge bases and documents: create, upload, list, reindex, and the ingestion pipeline."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge import audit
from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge import queue
from agentforge.knowledge.blobs import BlobStore
from agentforge.knowledge.chunking import chunk, embedding_text, normalize
from agentforge.knowledge.embeddings import EmbeddingTable, vector_literal
from agentforge.knowledge.parsing import ParseError, parse_file
from agentforge.knowledge.validation import validate_upload
from agentforge.models import CallContext, ModelError, ModelGateway

log = get_logger("agentforge.knowledge")
EMBED_BATCH = 16


class NotFoundError(Exception):
    pass


class DuplicateDocumentError(Exception):
    pass


class DuplicateNameError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: UUID
    name: str
    description: str
    document_count: int
    ready_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Document:
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


_DOCUMENT_COLUMNS = (
    "id, knowledge_base_id, filename, media_type, size_bytes, status, error, page_count, "
    "chunk_count, created_at, updated_at"
)
# Only constants are interpolated into this statement.
_INSERT_DOCUMENT = (
    "INSERT INTO app.documents (id, org_id, "  # noqa: S608
    "knowledge_base_id, filename, media_type, size_bytes, sha256, blob_key, uploaded_by) "
    "VALUES (:id, :o, :k, :f, :m, :z, :s, :b, :u) "
    f"RETURNING {_DOCUMENT_COLUMNS}"
)


@dataclass(frozen=True, slots=True)
class Actor:
    """Who performed an action, for the audit log."""

    user_id: UUID | None
    email: str | None
    ip: str | None = None


# Runs inside the deletion transaction with the ids of the documents being deleted. Modules
# that keep copies of document text (answer evidence, model-call prompts) redact them here.
PurgeHook = Callable[[AsyncSession, list[UUID]], Awaitable[None]]


class KnowledgeService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        blobs: BlobStore,
        gateway: ModelGateway,
        *,
        embedding_model: str,
        embedding_table: EmbeddingTable,
        purge_hooks: Sequence[PurgeHook] = (),
    ) -> None:
        self._sessions = sessions
        self._blobs = blobs
        self._gateway = gateway
        self._embedding_model = embedding_model
        self._table = embedding_table
        self._purge_hooks = list(purge_hooks)

    # --- Knowledge bases ---------------------------------------------------------------------

    async def create_kb(
        self, org_id: UUID, user_id: UUID | None, name: str, description: str = ""
    ) -> UUID:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            workspace = (
                await db.execute(text("SELECT id FROM app.workspaces ORDER BY created_at LIMIT 1"))
            ).scalar_one()
            try:
                async with db.begin_nested():
                    kb_id: UUID = (
                        await db.execute(
                            text(
                                "INSERT INTO app.knowledge_bases "
                                "(org_id, workspace_id, name, description, created_by) "
                                "VALUES (:o, :w, :n, :d, :u) RETURNING id"
                            ),
                            {
                                "o": org_id,
                                "w": workspace,
                                "n": name.strip(),
                                "d": description,
                                "u": user_id,
                            },
                        )
                    ).scalar_one()
            except IntegrityError as error:
                raise DuplicateNameError(
                    f"A knowledge base named {name!r} already exists."
                ) from error
        return kb_id

    async def list_kbs(self, org_id: UUID) -> list[KnowledgeBase]:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    """
                    SELECT k.id, k.name, k.description, k.created_at,
                           count(d.id) AS document_count,
                           count(d.id) FILTER (WHERE d.status = 'ready') AS ready_count
                    FROM app.knowledge_bases k
                    LEFT JOIN app.documents d ON d.knowledge_base_id = k.id
                    GROUP BY k.id ORDER BY k.created_at
                    """
                )
            )
            return [
                KnowledgeBase(
                    r.id, r.name, r.description, r.document_count, r.ready_count, r.created_at
                )
                for r in rows
            ]

    async def _require_kb(self, db: AsyncSession, kb_id: UUID) -> None:
        found = (
            await db.execute(text("SELECT 1 FROM app.knowledge_bases WHERE id = :k"), {"k": kb_id})
        ).scalar_one_or_none()
        if found is None:
            raise NotFoundError("Knowledge base not found")

    # --- Documents -----------------------------------------------------------------------------

    async def add_document(
        self,
        org_id: UUID,
        kb_id: UUID,
        user_id: UUID | None,
        upload: Path,
        filename: str,
        size: int,
    ) -> Document:
        validated = validate_upload(upload, filename, size)
        digest = await asyncio.to_thread(_sha256, upload)
        document_id = uuid4()
        blob_key = f"org/{org_id}/kb/{kb_id}/{document_id}"
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            await self._require_kb(db, kb_id)
            exists = (
                await db.execute(
                    text(
                        "SELECT filename FROM app.documents "
                        "WHERE knowledge_base_id = :k AND sha256 = :s"
                    ),
                    {"k": kb_id, "s": digest},
                )
            ).scalar_one_or_none()
            if exists is not None:
                raise DuplicateDocumentError(
                    f"This file is already in the knowledge base as {exists!r}."
                )
            await asyncio.to_thread(self._blobs.put, blob_key, upload)
            try:
                row = (
                    await db.execute(
                        text(_INSERT_DOCUMENT),
                        {
                            "id": document_id,
                            "o": org_id,
                            "k": kb_id,
                            "f": validated.filename,
                            "m": validated.media_type,
                            "z": size,
                            "s": digest,
                            "b": blob_key,
                            "u": user_id,
                        },
                    )
                ).one()
                await queue.enqueue(db, org_id, document_id)
            except Exception:
                self._blobs.delete(blob_key)
                raise
        log.info("document_uploaded", document_id=str(document_id), media_type=validated.media_type)
        return Document(**row._mapping)  # pyright: ignore[reportPrivateUsage]

    async def list_documents(self, org_id: UUID, kb_id: UUID) -> list[Document]:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            await self._require_kb(db, kb_id)
            rows = await db.execute(
                text(
                    f"SELECT {_DOCUMENT_COLUMNS} FROM app.documents "  # noqa: S608
                    "WHERE knowledge_base_id = :k ORDER BY created_at DESC"
                ),
                {"k": kb_id},
            )
            return [Document(**r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]

    async def reindex(self, org_id: UUID, document_id: UUID) -> None:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            updated = await db.execute(
                text(
                    "UPDATE app.documents SET status = 'queued', error = NULL, updated_at = now() "
                    "WHERE id = :d RETURNING id"
                ),
                {"d": document_id},
            )
            if updated.one_or_none() is None:
                raise NotFoundError("Document not found")
            await queue.enqueue(db, org_id, document_id)

    # --- Deletion (NFR-13: rows, vectors, and blobs, synchronously) --------------------------

    async def delete_document(self, org_id: UUID, document_id: UUID, actor: Actor) -> None:
        await self._delete(org_id, actor, "document.delete", document_id=document_id)

    async def delete_kb(self, org_id: UUID, kb_id: UUID, actor: Actor) -> None:
        await self._delete(org_id, actor, "knowledge_base.delete", kb_id=kb_id)

    async def _delete(
        self,
        org_id: UUID,
        actor: Actor,
        action: str,
        *,
        document_id: UUID | None = None,
        kb_id: UUID | None = None,
    ) -> None:
        """Delete in one transaction (chunks and embeddings cascade; purge hooks redact copies
        of the text kept elsewhere, such as answer evidence), then remove the blobs."""
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            if document_id is not None:
                rows = (
                    await db.execute(
                        text("SELECT id, blob_key FROM app.documents WHERE id = :d FOR UPDATE"),
                        {"d": document_id},
                    )
                ).all()
                if not rows:
                    raise NotFoundError("Document not found")
            else:
                assert kb_id is not None  # noqa: S101 - one of the two is always given
                await self._require_kb(db, kb_id)
                rows = (
                    await db.execute(
                        text(
                            "SELECT id, blob_key FROM app.documents "
                            "WHERE knowledge_base_id = :k FOR UPDATE"
                        ),
                        {"k": kb_id},
                    )
                ).all()
            document_ids: list[UUID] = [r.id for r in rows]
            for hook in self._purge_hooks:
                await hook(db, document_ids)
            await db.execute(
                text("DELETE FROM queue.ingestion_jobs WHERE document_id = ANY(:ids)"),
                {"ids": document_ids},
            )
            if document_id is not None:
                await db.execute(
                    text("DELETE FROM app.documents WHERE id = :d"), {"d": document_id}
                )
            else:
                await db.execute(
                    text("DELETE FROM app.knowledge_bases WHERE id = :k"), {"k": kb_id}
                )
        for r in rows:
            await asyncio.to_thread(self._blobs.delete, r.blob_key)
        target = f"document:{document_id}" if document_id else f"knowledge_base:{kb_id}"
        await audit.record(
            self._sessions,
            audit.AuditEvent(
                action,
                "success",
                org_id=org_id,
                actor_user_id=actor.user_id,
                actor_email=actor.email,
                target=target,
                ip=actor.ip,
                details={"documents": len(rows)},
            ),
        )
        log.info("deleted", target=target, documents=len(rows))

    async def residuals(self, org_id: UUID, document_ids: list[UUID]) -> dict[str, int]:
        """Verification query: everything that would still hold these documents' content."""
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            counts = (
                await db.execute(
                    text(
                        f"""
                        SELECT
                          (SELECT count(*) FROM app.documents WHERE id = ANY(:ids)) AS documents,
                          (SELECT count(*) FROM app.chunks WHERE document_id = ANY(:ids)) AS chunks,
                          (SELECT count(*) FROM app.{self._table.name} e
                             WHERE NOT EXISTS (SELECT 1 FROM app.chunks c WHERE c.id = e.chunk_id)
                          ) AS orphan_embeddings,
                          (SELECT count(*) FROM queue.ingestion_jobs
                             WHERE document_id = ANY(:ids)) AS jobs
                        """  # noqa: S608 - fixed table name
                    ),
                    {"ids": document_ids},
                )
            ).one()
        return dict(counts._mapping)  # pyright: ignore[reportPrivateUsage]

    # --- Ingestion (runs in the worker) ------------------------------------------------------

    async def _set_status(
        self, org_id: UUID, document_id: UUID, status: str, error: str | None = None
    ) -> None:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            await db.execute(
                text(
                    "UPDATE app.documents SET status = :s, error = :e, updated_at = now() "
                    "WHERE id = :d"
                ),
                {"s": status, "e": error, "d": document_id},
            )

    async def process_next_job(self) -> bool:
        """Claim and process one ingestion job. Returns False when the queue is empty."""
        job = await queue.claim(self._sessions)
        if job is None:
            return False
        try:
            await self._ingest(job)
        except ParseError as error:
            await self._set_status(job.org_id, job.subject_id, "failed", str(error))
            await queue.finish(self._sessions, job, ok=False)
            log.warning("ingestion_failed", document_id=str(job.subject_id), reason="parse")
        except ModelError as error:
            if await queue.retry_later(self._sessions, job):
                await self._set_status(
                    job.org_id,
                    job.subject_id,
                    "queued",
                    f"Waiting for the embedding model: {error}",
                )
            else:
                await self._set_status(
                    job.org_id, job.subject_id, "failed", f"Embedding failed repeatedly: {error}"
                )
            log.warning("ingestion_deferred", document_id=str(job.subject_id), error=str(error))
        except _DocumentGoneError:
            await queue.finish(self._sessions, job, ok=True)
        else:
            await queue.finish(self._sessions, job, ok=True)
        return True

    async def _ingest(self, job: queue.Job) -> None:
        context = TenantContext(job.org_id)
        async with tenant_session(self._sessions, context) as db:
            row = (
                await db.execute(
                    text(
                        "UPDATE app.documents SET status = 'processing', error = NULL, "
                        "updated_at = now() WHERE id = :d "
                        "RETURNING knowledge_base_id, blob_key, media_type"
                    ),
                    {"d": job.subject_id},
                )
            ).one_or_none()
        if row is None:
            raise _DocumentGoneError
        blocks = await asyncio.to_thread(parse_file, self._blobs.path(row.blob_key), row.media_type)
        document = normalize(blocks)
        chunks = chunk(document)

        call = CallContext(org_id=job.org_id, purpose="ingest")
        vectors: list[list[float]] = []
        prefix = self._table.document_prefix
        for start in range(0, len(chunks), EMBED_BATCH):
            batch = [
                prefix + embedding_text(c.section_path, c.content)
                for c in chunks[start : start + EMBED_BATCH]
            ]
            vectors += await self._gateway.embed(call, batch, model=self._embedding_model)

        # Replace this document's chunks atomically: a crash or a rerun never leaves
        # duplicates or a half-indexed document.
        async with tenant_session(self._sessions, context) as db:
            # Lock the document: a concurrent delete either finishes first (we stop here) or
            # waits for this transaction and then removes the new chunks with the document.
            still_there = (
                await db.execute(
                    text("SELECT 1 FROM app.documents WHERE id = :d FOR UPDATE"),
                    {"d": job.subject_id},
                )
            ).scalar_one_or_none()
            if still_there is None:
                raise _DocumentGoneError
            await db.execute(
                text("DELETE FROM app.chunks WHERE document_id = :d"), {"d": job.subject_id}
            )
            chunk_ids = [uuid4() for _ in chunks]
            if chunks:
                await db.execute(
                    text(
                        "INSERT INTO app.chunks (id, org_id, knowledge_base_id, document_id, "
                        "ordinal, content, page_start, page_end, char_start, char_end, "
                        "section_path, token_estimate) "
                        "VALUES (:id, :o, :k, :d, :n, :c, :ps, :pe, :cs, :ce, "
                        "CAST(:sp AS jsonb), :t)"
                    ),
                    [
                        {
                            "id": chunk_id,
                            "o": job.org_id,
                            "k": row.knowledge_base_id,
                            "d": job.subject_id,
                            "n": c.ordinal,
                            "c": c.content,
                            "ps": c.page_start,
                            "pe": c.page_end,
                            "cs": c.char_start,
                            "ce": c.char_end,
                            "sp": json.dumps(c.section_path),
                            "t": c.token_estimate,
                        }
                        for chunk_id, c in zip(chunk_ids, chunks, strict=True)
                    ],
                )
                await db.execute(
                    text(
                        f"INSERT INTO app.{self._table.name} "  # noqa: S608 - fixed table name
                        "(chunk_id, org_id, knowledge_base_id, embedding) "
                        "VALUES (:id, :o, :k, CAST(:v AS vector))"
                    ),
                    [
                        {
                            "id": chunk_id,
                            "o": job.org_id,
                            "k": row.knowledge_base_id,
                            "v": vector_literal(v),
                        }
                        for chunk_id, v in zip(chunk_ids, vectors, strict=True)
                    ],
                )
            await db.execute(
                text(
                    "UPDATE app.documents SET status = 'ready', error = NULL, page_count = :p, "
                    "chunk_count = :n, normalized_text = :t, page_map = CAST(:m AS jsonb), "
                    "updated_at = now() WHERE id = :d"
                ),
                {
                    "p": document.page_count,
                    "n": len(chunks),
                    "t": document.text,
                    "m": json.dumps(document.page_map),
                    "d": job.subject_id,
                },
            )
        log.info("document_indexed", document_id=str(job.subject_id), chunks=len(chunks))


class _DocumentGoneError(Exception):
    """The document was deleted after its job was queued."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()
