"""
Ties generation together with citation enforcement.

Status semantics on GenerationResult:
  - "answered"             -- every citation passed grounding and entailment
  - "partially_supported"  -- answer kept, but some citations failed entailment
                               (surfaced to the caller/UI, not silently hidden)
  - "insufficient_evidence"-- model itself said the context wasn't enough
  - "citation_violation"   -- a citation pointed at a chunk_id that was never
                               retrieved (hallucinated source); the answer is
                               replaced with a safe fallback rather than shown
"""

from __future__ import annotations

from dataclasses import dataclass, field

import ollama

from src.generate.citation_checker import EntailmentResult, entailment_check, grounding_check
from src.generate.prompts import SYSTEM_PROMPT, build_user_prompt
from src.generate.schemas import CitedAnswer

FALLBACK_MESSAGE = (
    "I don't have enough verifiably-sourced information in the provided documents "
    "to answer this question confidently."
)

DEFAULT_MODEL = "qwen2.5:7b"


@dataclass
class GenerationResult:
    status: str
    answer: str
    citations: list[dict]
    grounding_violations: list[str] = field(default_factory=list)
    entailment_results: list[EntailmentResult] = field(default_factory=list)


class RAGGenerator:
    def __init__(self, model: str = DEFAULT_MODEL, run_entailment_check: bool = True):
        self.model = model
        self.run_entailment_check = run_entailment_check

    def answer(self, question: str, context_chunks: list[dict]) -> GenerationResult:
        """context_chunks: reranked chunks, each a dict with 'chunk_id' and 'text'
        (e.g. RerankedChunk.payload merged with chunk_id)."""
        retrieved_ids = {c["chunk_id"] for c in context_chunks}

        resp = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(question, context_chunks)},
            ],
            format=CitedAnswer.model_json_schema(),
            options={"temperature": 0.0},
        )
        parsed = CitedAnswer.model_validate_json(resp.message.content)

        if parsed.insufficient_evidence:
            return GenerationResult(status="insufficient_evidence", answer=FALLBACK_MESSAGE, citations=[])

        if not parsed.citations:
            # the model made claims but cited nothing -- this is itself a
            # citation-enforcement failure, not a trustworthy "answered" result
            return GenerationResult(status="uncited_answer", answer=FALLBACK_MESSAGE, citations=[])

        violations = grounding_check(parsed.citations, retrieved_ids)
        if violations:
            return GenerationResult(
                status="citation_violation",
                answer=FALLBACK_MESSAGE,
                citations=[],
                grounding_violations=violations,
            )

        chunk_text_by_id = {c["chunk_id"]: c["text"] for c in context_chunks}
        entailment_results: list[EntailmentResult] = []
        if self.run_entailment_check:
            for citation in parsed.citations:
                entailment_results.append(
                    entailment_check(citation, chunk_text_by_id[citation.chunk_id], model=self.model)
                )

        all_unsupported = bool(entailment_results) and not any(r.supported for r in entailment_results)
        if all_unsupported:
            return GenerationResult(
                status="citation_violation",
                answer=FALLBACK_MESSAGE,
                citations=[],
                entailment_results=entailment_results,
            )

        status = "answered" if all(r.supported for r in entailment_results) or not entailment_results else "partially_supported"
        return GenerationResult(
            status=status,
            answer=parsed.answer,
            citations=[c.model_dump() for c in parsed.citations],
            entailment_results=entailment_results,
        )
