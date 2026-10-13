"""
Two-stage citation enforcement.

1. grounding_check -- pure, cheap, no LLM call: catches hallucinated sources by
   confirming every cited chunk_id actually came from the retrieved context.
2. entailment_check -- one LLM-judge call per citation: confirms the cited
   chunk's text actually supports the specific claim, not just the same topic.
"""

from __future__ import annotations

from dataclasses import dataclass

import ollama

from src.generate.prompts import ENTAILMENT_SYSTEM_PROMPT, build_entailment_prompt
from src.generate.schemas import Citation, EntailmentVerdict


def grounding_check(citations: list[Citation], retrieved_chunk_ids: set[str]) -> list[str]:
    """Returns the chunk_ids that were cited but never appeared in the
    retrieved context -- i.e. hallucinated sources."""
    return [c.chunk_id for c in citations if c.chunk_id not in retrieved_chunk_ids]


@dataclass
class EntailmentResult:
    chunk_id: str
    claim: str
    supported: bool
    reasoning: str


def entailment_check(
    citation: Citation,
    chunk_text: str,
    model: str = "qwen2.5:7b",
) -> EntailmentResult:
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": ENTAILMENT_SYSTEM_PROMPT},
            {"role": "user", "content": build_entailment_prompt(citation.claim, chunk_text)},
        ],
        format=EntailmentVerdict.model_json_schema(),
        options={"temperature": 0.0},
    )
    verdict = EntailmentVerdict.model_validate_json(resp.message.content)
    return EntailmentResult(
        chunk_id=citation.chunk_id,
        claim=citation.claim,
        supported=verdict.supported,
        reasoning=verdict.reasoning,
    )
