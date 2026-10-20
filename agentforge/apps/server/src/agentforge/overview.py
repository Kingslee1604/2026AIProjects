"""The overview dashboard: real counts for one organization, read in one tenant transaction.

Nothing here is estimated or invented: every number is a query over the organization's own
rows (RLS applies), and the model status is what Ollama reports right now.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.models import ModelGateway
from agentforge.models.providers import OllamaProvider

# Run events worth showing as activity (item-level events are too fine-grained here).
ACTIVITY_TYPES = (
    "run_started",
    "review_waiting",
    "review_completed",
    "run_succeeded",
    "run_failed",
    "run_cancelled",
)


@dataclass(frozen=True, slots=True)
class ModelStatus:
    provider: str  # "ollama" or "fake"
    reachable: bool
    chat_model: str
    chat_model_installed: bool
    embedding_model: str
    embedding_model_installed: bool


@dataclass(frozen=True, slots=True)
class Activity:
    run_id: UUID
    title: str
    type: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Overview:
    runs_by_status: dict[str, int]
    answers_to_review: int
    reviews_open: int
    # Items of runs that are queued or running, by item status (the live pipeline).
    pipeline: dict[str, int]
    # Kept sentences in answers and run items of the last 30 days, by how they were proven.
    sentences_30d: dict[str, int]
    questions_7d: dict[str, int]
    model_calls_7d: int
    model_timeouts_7d: int
    tokens_7d: int
    call_seconds_p50_7d: float | None
    knowledge_bases: int
    documents_by_status: dict[str, int]
    documents_flagged: int
    activity: list[Activity] = field(default_factory=lambda: [])
    model: ModelStatus | None = None


def _counts(rows: Any) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in rows}


class OverviewService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: ModelGateway,
        *,
        model_provider: str,
        chat_model: str,
        embedding_model: str,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._provider = model_provider
        self._chat = chat_model
        self._embedding = embedding_model

    async def get(self, org_id: UUID) -> Overview:
        stats, model = await asyncio.gather(self._stats(org_id), self.model_status())
        return Overview(model=model, **stats)

    async def _stats(self, org_id: UUID) -> dict[str, Any]:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:

            async def rows(sql: str, **params: Any) -> Any:
                return (await db.execute(text(sql), params)).all()

            review = (
                await rows(
                    "SELECT count(DISTINCT b.id), count(*) FILTER (WHERE ri.decision IS NULL) "
                    "FROM app.review_batches b JOIN app.review_items ri ON ri.batch_id = b.id "
                    "WHERE b.status = 'open'"
                )
            )[0]
            calls = (
                await rows(
                    "SELECT count(*), count(*) FILTER (WHERE status = 'timeout'), "
                    "coalesce(sum(coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)), 0),"
                    " percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) "
                    "FROM app.llm_calls WHERE created_at > now() - interval '7 days'"
                )
            )[0]
            flagged = (
                await rows("SELECT count(*) FROM app.documents WHERE risk_flags <> '[]'::jsonb")
            )[0][0]
            kbs = (await rows("SELECT count(*) FROM app.knowledge_bases"))[0][0]
            activity = await rows(
                "SELECT e.run_id, r.title, e.type, e.created_at FROM app.run_events e "
                "JOIN app.runs r ON r.id = e.run_id "
                "WHERE e.type = ANY(:types) ORDER BY e.seq DESC LIMIT 8",
                types=list(ACTIVITY_TYPES),
            )
            return {
                "runs_by_status": _counts(
                    await rows("SELECT status, count(*) FROM app.runs GROUP BY status")
                ),
                "answers_to_review": int(review[1]),
                "reviews_open": int(review[0]),
                "pipeline": _counts(
                    await rows(
                        "SELECT i.status, count(*) FROM app.run_items i "
                        "JOIN app.runs r ON r.id = i.run_id "
                        "WHERE r.status IN ('queued', 'running') GROUP BY i.status"
                    )
                ),
                "sentences_30d": _counts(
                    await rows(
                        "SELECT c->>'status', count(*) FROM ("
                        "  SELECT claims FROM app.qa_answers "
                        "  WHERE created_at > now() - interval '30 days' "
                        "  UNION ALL SELECT claims FROM app.run_items "
                        "  WHERE completed_at > now() - interval '30 days'"
                        ") a, jsonb_array_elements(a.claims) c "
                        "WHERE coalesce((c->>'kept')::bool, false) GROUP BY 1"
                    )
                ),
                "questions_7d": _counts(
                    await rows(
                        "SELECT status, count(*) FROM app.qa_answers "
                        "WHERE created_at > now() - interval '7 days' GROUP BY status"
                    )
                ),
                "model_calls_7d": int(calls[0]),
                "model_timeouts_7d": int(calls[1]),
                "tokens_7d": int(calls[2]),
                "call_seconds_p50_7d": round(float(calls[3]) / 1000, 1)
                if calls[3] is not None
                else None,
                "knowledge_bases": int(kbs),
                "documents_by_status": _counts(
                    await rows("SELECT status, count(*) FROM app.documents GROUP BY status")
                ),
                "documents_flagged": int(flagged),
                "activity": [Activity(r[0], r[1], r[2], r[3]) for r in activity],
            }

    async def model_status(self) -> ModelStatus:
        chat = self._chat.split(":", 1)[1] if ":" in self._chat else self._chat
        embedding = self._embedding.split(":", 1)[1] if ":" in self._embedding else self._embedding
        if self._provider == "fake":
            return ModelStatus("fake", True, "fake", True, "fake", True)
        provider = self._gateway.provider("ollama")
        installed = (
            await provider.installed_models() if isinstance(provider, OllamaProvider) else None
        )
        names = set(installed or [])
        return ModelStatus(
            provider="ollama",
            reachable=installed is not None,
            chat_model=chat,
            chat_model_installed=chat in names,
            embedding_model=embedding,
            embedding_model_installed=embedding in names,
        )
