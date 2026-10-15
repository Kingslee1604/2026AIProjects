"""Questionnaire planner (R1-32): classify items, split compound questions, rewrite searches.

Batches of items share one model call (CPU-only machines). The model's output is advisory and
validated: an item it skips or garbles falls back to a pass-through plan (answer the question
as written), so a bad plan can never lose a question.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from agentforge.agents.prompts import neutralize_untrusted
from agentforge.agents.registry import AgentRegistry
from agentforge.models import CallContext, Message, ModelGateway

BATCH_SIZE = 8
MAX_QUERIES = 3
MAX_PARTS = 4

Kind = Literal["question", "instruction", "not_a_question"]


class PlannedItem(BaseModel):
    index: int
    kind: Kind
    search_queries: list[str] = Field(max_length=MAX_QUERIES)
    parts: list[str] = Field(max_length=MAX_PARTS)


class PlanBatch(BaseModel):
    items: list[PlannedItem] = Field(max_length=BATCH_SIZE)


@dataclass(frozen=True, slots=True)
class ItemToPlan:
    index: int
    question: str
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ItemPlan:
    kind: Kind = "question"
    search_queries: list[str] = field(default_factory=lambda: [])
    parts: list[str] = field(default_factory=lambda: [])
    source: Literal["model", "fallback"] = "fallback"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _clean(values: Sequence[str], limit: int, max_chars: int) -> list[str]:
    cleaned = [" ".join(v.split())[:max_chars] for v in values if v and v.strip()]
    return list(dict.fromkeys(cleaned))[:limit]


def _validated(item: PlannedItem) -> ItemPlan:
    if item.kind != "question":
        return ItemPlan(kind=item.kind, source="model")
    parts = _clean(item.parts, MAX_PARTS, 500)
    return ItemPlan(
        kind="question",
        search_queries=_clean(item.search_queries, MAX_QUERIES, 120),
        parts=parts if len(parts) >= 2 else [],  # one "part" is just the question again
        source="model",
    )


def render_batch(items: Sequence[ItemToPlan]) -> str:
    lines = [
        f"[{item.index}] {'(' + neutralize_untrusted(item.section) + ') ' if item.section else ''}"
        f"{neutralize_untrusted(item.question)}"
        for item in items
    ]
    return "Plan these questionnaire items (one plan per index):\n\n" + "\n".join(lines)


class Planner:
    def __init__(self, gateway: ModelGateway, registry: AgentRegistry, chat_model: str) -> None:
        self._gateway = gateway
        self._registry = registry
        self._chat_model = chat_model

    async def plan_batch(
        self,
        org_id: UUID,
        items: Sequence[ItemToPlan],
        *,
        run_id: UUID | None = None,
        step_id: UUID | None = None,
    ) -> tuple[dict[int, ItemPlan], str]:
        """Plans keyed by item index (every index present), and the planner version id."""
        planner = await self._registry.version(org_id, "planner")
        definition = planner.definition
        model = self._chat_model if definition.model == "default" else definition.model
        output, _ = await self._gateway.generate_structured(
            CallContext(org_id, "plan", run_id, step_id),
            [Message("system", definition.system_prompt), Message("user", render_batch(items))],
            PlanBatch,
            model=model,
            fallbacks=list(definition.fallbacks),
            temperature=definition.temperature,
            max_tokens=definition.max_tokens,
        )
        wanted = {item.index for item in items}
        plans = {p.index: _validated(p) for p in output.items if p.index in wanted}
        for index in wanted - plans.keys():
            plans[index] = ItemPlan()  # skipped by the model: answer as written
        return plans, str(planner.id)
