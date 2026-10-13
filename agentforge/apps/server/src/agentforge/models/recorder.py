"""Persists gateway call records to app.llm_calls under the caller's tenant."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.telemetry import current_trace_id
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.models.types import CallRecord

_INSERT = text(
    """
    INSERT INTO app.llm_calls
      (org_id, run_id, purpose, model, model_digest, status, attempt, latency_ms,
       prompt_tokens, completion_tokens, error_class, request, response, json_schema, trace_id)
    VALUES
      (:org_id, :run_id, :purpose, :model, :model_digest, :status, :attempt, :latency_ms,
       :prompt_tokens, :completion_tokens, :error_class, CAST(:request AS jsonb), :response,
       CAST(:json_schema AS jsonb), :trace_id)
    """
)


class DbRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(self, record: CallRecord) -> None:
        context = record.context
        async with tenant_session(self._sessions, TenantContext(context.org_id)) as db:
            await db.execute(
                _INSERT,
                {
                    "org_id": context.org_id,
                    "run_id": context.run_id,
                    "purpose": context.purpose,
                    "model": record.model,
                    "model_digest": record.model_digest,
                    "status": record.status,
                    "attempt": record.attempt,
                    "latency_ms": record.latency_ms,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "error_class": record.error_class,
                    "request": json.dumps(record.request),
                    "response": record.response,
                    "json_schema": json.dumps(record.json_schema) if record.json_schema else None,
                    "trace_id": current_trace_id(),
                },
            )
