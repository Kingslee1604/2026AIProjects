"""Hybrid retrieval: pgvector HNSW + Postgres full-text, fused with Reciprocal Rank Fusion.

Both searches are filtered by RLS (tenant) and an explicit knowledge-base list. pgvector's
iterative index scan keeps filtered vector search from returning too few results
(architecture §10.2).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge.embeddings import EmbeddingTable, vector_literal
from agentforge.models import CallContext, ModelGateway

RRF_K = 60
CANDIDATES = 30  # per channel, before fusion


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: UUID
    document_id: UUID
    filename: str
    content: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: list[str]
    score: float  # fused RRF score
    vector_rank: int | None
    text_rank: int | None
    vector_distance: float | None = None
    text_score: float | None = None
    channels: list[str] = field(default_factory=lambda: [])


_TERM = re.compile(r"[a-z0-9]+")


def or_query(question: str) -> str:
    """A to_tsquery string matching ANY word of a natural-language question.

    websearch_to_tsquery requires every word, so "how long are logs kept" misses a passage
    that never says "long". OR semantics plus ts_rank_cd rank passages by how many words they
    share. Stop words are dropped by the 'english' configuration; only [a-z0-9] terms are kept,
    so no tsquery syntax can be injected.
    """
    terms = dict.fromkeys(_TERM.findall(question.lower()))  # dedupe, keep order
    return " | ".join(terms)


def rrf(rankings: Sequence[Sequence[UUID]], k: int = RRF_K) -> dict[UUID, float]:
    """Reciprocal Rank Fusion: score = sum over rankings of 1 / (k + rank), rank from 1."""
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class Retriever:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: ModelGateway,
        *,
        embedding_model: str,
        embedding_table: EmbeddingTable,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._model = embedding_model
        self._table = embedding_table

    async def search(
        self,
        org_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        query: str,
        *,
        k: int = 8,
        run_id: UUID | None = None,
    ) -> list[SearchHit]:
        if not knowledge_base_ids or not query.strip():
            return []
        [query_vector] = await self._gateway.embed(
            CallContext(org_id=org_id, purpose="retrieve", run_id=run_id),
            [self._table.query_prefix + query],
            model=self._model,
        )
        kb_ids = list(knowledge_base_ids)
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            await db.execute(
                text(
                    "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                    "set_config('hnsw.ef_search', '100', true)"
                )
            )
            vector_rows = (
                await db.execute(
                    text(
                        f"SELECT chunk_id, embedding <=> CAST(:q AS vector) AS distance "  # noqa: S608
                        f"FROM app.{self._table.name} WHERE knowledge_base_id = ANY(:kbs) "
                        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"
                    ),
                    {"q": vector_literal(query_vector), "kbs": kb_ids, "n": CANDIDATES},
                )
            ).all()
            terms = or_query(query)
            text_rows: list[Row[Any]] = []
            if terms:  # a query of only punctuation has no searchable words
                text_rows = list(
                    (
                        await db.execute(
                            text(
                                "SELECT id, ts_rank_cd(fts, q) AS score "
                                "FROM app.chunks, to_tsquery('english', :query) AS q "
                                "WHERE fts @@ q AND knowledge_base_id = ANY(:kbs) "
                                "ORDER BY score DESC, id LIMIT :n"
                            ),
                            {"query": terms, "kbs": kb_ids, "n": CANDIDATES},
                        )
                    ).all()
                )
            vector_order: list[UUID] = [r.chunk_id for r in vector_rows]
            text_order: list[UUID] = [r.id for r in text_rows]
            fused = rrf([vector_order, text_order])
            top = sorted(fused, key=lambda c: (-fused[c], str(c)))[:k]
            if not top:
                return []
            details = {
                r.id: r
                for r in await db.execute(
                    text(
                        "SELECT c.id, c.document_id, d.filename, c.content, c.page_start, "
                        "c.page_end, c.char_start, c.char_end, c.section_path "
                        "FROM app.chunks c JOIN app.documents d ON d.id = c.document_id "
                        "WHERE c.id = ANY(:ids)"
                    ),
                    {"ids": top},
                )
            }
        distance = {r.chunk_id: float(r.distance) for r in vector_rows}
        text_score = {r.id: float(r.score) for r in text_rows}
        hits: list[SearchHit] = []
        for chunk_id in top:
            row = details[chunk_id]
            vector_rank = vector_order.index(chunk_id) + 1 if chunk_id in distance else None
            text_rank = text_order.index(chunk_id) + 1 if chunk_id in text_score else None
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    document_id=row.document_id,
                    filename=row.filename,
                    content=row.content,
                    page_start=row.page_start,
                    page_end=row.page_end,
                    char_start=row.char_start,
                    char_end=row.char_end,
                    section_path=list(row.section_path),
                    score=round(fused[chunk_id], 6),
                    vector_rank=vector_rank,
                    text_rank=text_rank,
                    vector_distance=distance.get(chunk_id),
                    text_score=text_score.get(chunk_id),
                    channels=[c for c, r in (("vector", vector_rank), ("text", text_rank)) if r],
                )
            )
        return hits
