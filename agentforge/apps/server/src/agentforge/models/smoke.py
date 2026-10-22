"""`agentforge models smoke`: one structured call and one embedding against the configured
models, recorded in app.llm_calls. Proves the local model setup works end to end."""

import asyncio
import json
import time

from pydantic import BaseModel, Field

from agentforge.core.settings import Settings
from agentforge.db.engine import create_app_engine, create_sessionmaker
from agentforge.db.migrate import DEFAULT_ORG_ID
from agentforge.models.gateway import build_gateway
from agentforge.models.recorder import DbRecorder
from agentforge.models.types import CallContext, Message


class SmokeAnswer(BaseModel):
    answer: str = Field(description="One short sentence")
    confidence: float = Field(ge=0, le=1)


PROMPT = [
    Message("system", "Answer briefly. Reply with JSON only."),
    Message("user", "In one sentence: why should answers cite their sources?"),
]


async def _run(settings: Settings) -> dict[str, object]:
    engine = (
        create_app_engine(settings.database_url, pool_size=1) if settings.database_url else None
    )
    recorder = DbRecorder(create_sessionmaker(engine)) if engine else None
    gateway = build_gateway(settings, recorder) if recorder else build_gateway(settings)
    context = CallContext(org_id=DEFAULT_ORG_ID, purpose="smoke")
    try:
        started = time.perf_counter()
        parsed, result = await gateway.generate_structured(
            context,
            PROMPT,
            SmokeAnswer,
            model=settings.chat_model,
            fallbacks=settings.chat_fallback_models,
        )
        chat_seconds = time.perf_counter() - started
        started = time.perf_counter()
        vectors = await gateway.embed(
            context, ["evidence-grounded answers"], model=settings.embedding_model
        )
        return {
            "chat_model": result.model,
            "chat_model_digest": result.model_digest,
            "answer": parsed.answer,
            "confidence": parsed.confidence,
            "attempts": result.attempts,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "chat_seconds": round(chat_seconds, 2),
            "embedding_model": settings.embedding_model,
            "embedding_dimensions": len(vectors[0]),
            "embed_seconds": round(time.perf_counter() - started, 2),
            "recorded_in_db": recorder is not None,
        }
    finally:
        if engine:
            await engine.dispose()


def run_smoke(settings: Settings) -> int:
    print(json.dumps(asyncio.run(_run(settings)), indent=2))
    return 0
