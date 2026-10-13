"""
Generation-quality metrics computed against the RAGGenerator's own output.

Faithfulness and citation precision reuse the grounding/entailment checks
that already run inside src.generate.generator -- there is deliberately only
one place that judges whether a citation is trustworthy.
"""

from __future__ import annotations

import ollama

from src.generate.generator import GenerationResult
from src.generate.schemas import RelevanceVerdict

ANSWERED_STATUSES = {"answered", "partially_supported"}
REFUSAL_STATUSES = {"insufficient_evidence", "uncited_answer", "citation_violation"}


def citation_precision(result: GenerationResult) -> float | None:
    """Of the citations the model actually produced, what fraction were
    confirmed (by the entailment judge) to truly support their claim?
    None if no entailment check was run."""
    if not result.entailment_results:
        return None
    supported = sum(1 for r in result.entailment_results if r.supported)
    return supported / len(result.entailment_results)


def citation_recall(result: GenerationResult, expected_chunk_ids: set[str]) -> float | None:
    """Of the chunk_ids the gold answer says are required, how many did the
    model actually cite? None if the gold question has no expected citations
    (e.g. an adversarial/unanswerable question)."""
    if not expected_chunk_ids:
        return None
    cited_ids = {c["chunk_id"] for c in result.citations}
    return len(cited_ids & expected_chunk_ids) / len(expected_chunk_ids)


def faithfulness(result: GenerationResult) -> float | None:
    """Alias over citation_precision: fraction of claims the model made that
    are actually grounded in the cited source text."""
    return citation_precision(result)


def refused_correctly(result: GenerationResult, is_answerable: bool) -> bool:
    """For the refusal-accuracy metric: on an adversarial/unanswerable gold
    question, the system must NOT present an answer as trustworthy. On an
    answerable question, it must not refuse."""
    answered = result.status in ANSWERED_STATUSES
    if is_answerable:
        return answered
    return not answered


def answer_relevance(question: str, answer: str, model: str = "qwen2.5:7b") -> bool:
    """LLM-judge: does the answer actually address the question asked (as
    opposed to being on-topic but non-responsive)?"""
    resp = ollama.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You judge whether an answer directly addresses the question asked. "
                           "Ignore whether the answer is correct -- only judge relevance/responsiveness.",
            },
            {"role": "user", "content": f"Question: {question}\n\nAnswer: {answer}\n\nIs this answer relevant?"},
        ],
        format=RelevanceVerdict.model_json_schema(),
        options={"temperature": 0.0},
    )
    return RelevanceVerdict.model_validate_json(resp.message.content).relevant


def aggregate_generation_metrics(rows: list[dict]) -> dict[str, float]:
    """rows: list of {"citation_precision": float|None, "citation_recall": float|None,
    "refused_correctly": bool, "answer_relevant": bool|None}"""
    def _avg(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    refusal_vals = [r["refused_correctly"] for r in rows if "refused_correctly" in r]
    relevance_vals = [r["answer_relevant"] for r in rows if r.get("answer_relevant") is not None]

    return {
        "n": len(rows),
        "citation_precision": _avg("citation_precision"),
        "citation_recall": _avg("citation_recall"),
        "refusal_accuracy": (sum(refusal_vals) / len(refusal_vals)) if refusal_vals else None,
        "answer_relevance": (sum(relevance_vals) / len(relevance_vals)) if relevance_vals else None,
    }
