"""Scripted model behaviour for grounded-answer tests (no network, deterministic)."""

import json
import re
from collections.abc import Sequence
from typing import Any

from agentforge.models import Message

_BLOCK = re.compile(r'<evidence id="(E\d+)"[^>]*>\n(.*?)\n</evidence>', re.DOTALL)


def evidence_blocks(messages: Sequence[Message]) -> dict[str, str]:
    """The evidence passages the model was shown, by id."""
    return dict(_BLOCK.findall(messages[-1].content))


def first_sentence(content: str) -> str:
    body = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    return re.split(r"(?<=\.)\s", " ".join(body), maxsplit=1)[0]


def grounded_responder(
    model: str, messages: Sequence[Message], schema: dict[str, Any] | None
) -> str:
    """Drafter: one claim quoting E1 exactly. Verifier: always "supported"."""
    properties = (schema or {}).get("properties", {})
    if "verdict" in properties:
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
