"""Grounded behaviour for the fake model provider (MODEL_PROVIDER=fake: CI, UI work, demos
without a model). The drafter quotes the first sentence of the top passage; the verifier and
the relevance check agree. Deterministic, so browser tests can assert on what the UI shows."""

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from agentforge.models import Message

_BLOCK = re.compile(r'<evidence id="(E\d+)"[^>]*>\n(.*?)\n</evidence>', re.DOTALL)


def evidence_blocks(messages: Sequence[Message]) -> dict[str, str]:
    """The evidence passages the model was shown, by id."""
    return dict(_BLOCK.findall(messages[-1].content))


def first_sentence(content: str) -> str:
    body = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    return re.split(r"(?<=\.)\s", " ".join(body), maxsplit=1)[0]


Responder = Callable[[str, Sequence[Message], dict[str, Any] | None], str]


def _properties(schema: dict[str, Any] | None) -> dict[str, Any]:
    return (schema or {}).get("properties", {})


def with_grounded_answers(fallback: Responder) -> Responder:
    """Answer drafter and verifier prompts groundedly; everything else goes to `fallback`."""

    def responder(model: str, messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
        properties = _properties(schema)
        if "verdict" in properties or "claims" in properties or "relevance" in properties:
            return grounded_responder(model, messages, schema)
        if "items" in properties and "PlannedItem" in (schema or {}).get("$defs", {}):
            return passthrough_planner(messages)
        return fallback(model, messages, schema)

    return responder


_PLAN_LINE = re.compile(r"^\[(\d+)\]", re.MULTILINE)


def passthrough_planner(messages: Sequence[Message]) -> str:
    """Planner: every item is a question, answered as written."""
    indexes = [int(i) for i in _PLAN_LINE.findall(messages[-1].content)]
    return json.dumps(
        {
            "items": [
                {"index": i, "kind": "question", "search_queries": [], "parts": []} for i in indexes
            ]
        }
    )


def grounded_responder(
    model: str, messages: Sequence[Message], schema: dict[str, Any] | None
) -> str:
    """Drafter: one claim quoting the first sentence of E1. Verifier: always "supported".
    Relevance check: always "answers"."""
    if "relevance" in _properties(schema):
        return json.dumps({"relevance": "answers", "reason": "It answers the question."})
    if "verdict" in _properties(schema):
        return json.dumps({"verdict": "supported", "reason": "The passage states it."})
    blocks = evidence_blocks(messages)
    if not blocks:
        return json.dumps({"insufficient_evidence": True, "claims": []})
    quote = first_sentence(blocks["E1"])
    return json.dumps(
        {
            "insufficient_evidence": False,
            "claims": [{"text": quote, "citations": ["E1"], "quote": quote}],
        }
    )
