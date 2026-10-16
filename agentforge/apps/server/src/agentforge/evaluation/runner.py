"""Run an evaluation: load the corpus, measure retrieval, sweep the abstention gate, answer every
question with each chat model, and collect the results for the report.

The corpus goes into a temporary knowledge base that is deleted afterwards. Model calls made by
the answers are collected in memory (not written to llm_calls), so an evaluation leaves nothing
behind but its report.
"""

import asyncio
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.agents.registry import AgentRegistry
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.evaluation.dataset import Dataset, EvalQuestion, contains_quote
from agentforge.evaluation.metrics import (
    Passage,
    abstention,
    all_found_at,
    gold_ranks,
    mrr,
    overlap,
    ratio,
    recall_at,
    sweep,
)
from agentforge.knowledge.embeddings import EmbeddingTable, vector_literal
from agentforge.knowledge.retrieval import Retriever
from agentforge.knowledge.service import Actor, KnowledgeService
from agentforge.models import CallContext, ModelError, ModelGateway
from agentforge.models.gateway import Recorder
from agentforge.models.types import CallRecord
from agentforge.qa.pipeline import GroundedAnswer, GroundedAnswerer, QAConfig

RETRIEVAL_DEPTH = 20
RECALL_AT = (5, 10, 20)
ANN_K = 10
THRESHOLDS = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))  # 0.30 ... 0.80
INGEST_TIMEOUT_SECONDS = 900.0

Progress = Callable[[str], None]


@dataclass
class EvalDeps:
    """What an evaluation needs from the running system (wired by the CLI)."""

    sessions: async_sessionmaker[AsyncSession]
    knowledge: KnowledgeService
    retriever: Retriever
    gateway: ModelGateway
    agents: AgentRegistry
    embedding_model: str
    embedding_table: EmbeddingTable
    qa_config: QAConfig
    make_gateway: Callable[[Recorder], ModelGateway]  # a gateway recording into the given hook


def select(dataset: Dataset, which: str) -> list[EvalQuestion]:
    return [q for q in dataset.questions if which == "all" or q.demo]


async def _load_corpus(deps: EvalDeps, dataset: Dataset, org_id: UUID, progress: Progress) -> UUID:
    kb_id = await deps.knowledge.create_kb(
        org_id,
        None,
        f"eval-{dataset.name}-{uuid4().hex[:8]}",
        "Temporary knowledge base for `agentforge eval run`; deleted when it finishes.",
    )
    for path in dataset.corpus:
        await deps.knowledge.add_document(org_id, kb_id, None, path, path.name, path.stat().st_size)
    deadline = time.monotonic() + INGEST_TIMEOUT_SECONDS
    while True:
        documents = await deps.knowledge.list_documents(org_id, kb_id)
        failed = [d.filename for d in documents if d.status == "failed"]
        if failed:
            raise RuntimeError(f"indexing failed for {failed}")
        if all(d.status == "ready" for d in documents):
            break
        if time.monotonic() > deadline:
            raise TimeoutError("indexing the corpus took too long")
        # Index it ourselves; a running worker may take some of the jobs too.
        if not await deps.knowledge.process_next_job():
            await asyncio.sleep(0.5)
    chunks = sum(d.chunk_count or 0 for d in documents)
    progress(f"indexed {len(documents)} documents ({chunks} passages)")
    return kb_id


async def _vector_top(
    deps: EvalDeps, org_id: UUID, kb_id: UUID, vector: list[float], *, exact: bool
) -> list[UUID]:
    """Vector-only top-k as the retriever runs it (HNSW, iterative scan), or exactly (no index:
    a full scan), to measure what the approximate index loses."""
    async with tenant_session(deps.sessions, TenantContext(org_id)) as db:
        if exact:
            await db.execute(
                text(
                    "SELECT set_config('enable_indexscan', 'off', true), "
                    "set_config('enable_bitmapscan', 'off', true)"
                )
            )
        else:
            await db.execute(
                text(
                    "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                    "set_config('hnsw.ef_search', '100', true)"
                )
            )
        rows = await db.execute(
            text(
                f"SELECT chunk_id FROM app.{deps.embedding_table.name} "  # noqa: S608
                "WHERE knowledge_base_id = :kb ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"
            ),
            {"kb": kb_id, "q": vector_literal(vector), "n": ANN_K},
        )
        return [r.chunk_id for r in rows]


async def _retrieval(
    deps: EvalDeps,
    questions: Sequence[EvalQuestion],
    org_id: UUID,
    kb_id: UUID,
    progress: Progress,
) -> dict[str, Any]:
    per_question: list[dict[str, Any]] = []
    ranks_answerable: list[list[int | None]] = []
    overlaps: list[float] = []
    best: list[float | None] = []
    for q in questions:
        hits = await deps.retriever.search(org_id, [kb_id], q.question, k=RETRIEVAL_DEPTH)
        passages = [Passage(h.filename, h.content, h.vector_distance) for h in hits]
        ranks = gold_ranks(passages, q.evidence)
        if q.answerable:
            ranks_answerable.append(ranks)
        # What the answer pipeline's gate sees: the best distance among the passages it keeps.
        distances = [
            p.vector_distance for p in passages[: deps.qa_config.top_k] if p.vector_distance
        ]
        best.append(min(distances) if distances else None)
        [vector] = await deps.gateway.embed(
            CallContext(org_id, "retrieve"),
            [deps.embedding_table.query_prefix + q.question],
            model=deps.embedding_model,
        )
        approximate = await _vector_top(deps, org_id, kb_id, vector, exact=False)
        exact = await _vector_top(deps, org_id, kb_id, vector, exact=True)
        share = overlap(approximate, exact)
        if share is not None:
            overlaps.append(share)
        per_question.append(
            {
                "id": q.id,
                "answerable": q.answerable,
                "gold_ranks": ranks,
                "best_distance": None if best[-1] is None else round(best[-1], 4),
                "top": [{"file": p.filename, "distance": p.vector_distance} for p in passages[:3]],
            }
        )
    progress(f"retrieval measured on {len(questions)} questions")
    unanswerable = [not q.answerable for q in questions]
    rows = sweep(best, unanswerable, THRESHOLDS)
    current = deps.qa_config.max_distance
    # Fewest mistakes (refused answerable + answered unanswerable); ties go to the current
    # setting, then to the stricter threshold.
    recommended = min(
        rows,
        key=lambda r: (
            r.abstention.false_abstentions + r.abstention.missed,
            abs(r.threshold - current),
            r.threshold,
        ),
    )
    return {
        "questions": len(questions),
        "answerable": len(ranks_answerable),
        "depth": RETRIEVAL_DEPTH,
        "recall_at": {str(k): recall_at(ranks_answerable, k) for k in RECALL_AT},
        "all_quotes_at": {str(k): all_found_at(ranks_answerable, k) for k in RECALL_AT},
        "mrr": mrr(ranks_answerable),
        "ann_vs_exact_overlap_at_10": ratio(sum(overlaps), len(overlaps)),
        "gate": {
            "current": current,
            "recommended": recommended.threshold,
            "sweep": [{"threshold": r.threshold, **asdict(r.abstention)} for r in rows],
        },
        "per_question": per_question,
    }


def _claim_summary(result: GroundedAnswer, dataset: Dataset) -> list[dict[str, Any]]:
    files = {e.id: e.filename for e in result.evidence}
    return [
        {
            "text": c.text,
            "status": c.status,
            "proven": c.proven,
            "cited": sorted({files.get(i, i) for i in c.citations}),
            "adversarial": any(dataset.is_adversarial(files.get(i, "")) for i in c.citations),
        }
        for c in result.claims
        if c.kept
    ]


def _cites_gold(result: GroundedAnswer, q: EvalQuestion) -> bool:
    passages = {e.id: e for e in result.evidence}
    for claim in result.claims:
        if not claim.kept:
            continue
        for citation in claim.citations:
            passage = passages.get(citation)
            if passage and any(contains_quote(passage.content, g.quote) for g in q.evidence):
                return True
    return False


def _percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))], 1)


async def _answers(
    deps: EvalDeps,
    dataset: Dataset,
    questions: Sequence[EvalQuestion],
    org_id: UUID,
    kb_id: UUID,
    model: str,
    progress: Progress,
) -> dict[str, Any]:
    calls: list[CallRecord] = []

    async def collect(record: CallRecord) -> None:
        calls.append(record)

    answerer = GroundedAnswerer(
        deps.make_gateway(collect),
        deps.retriever,
        deps.agents,
        replace(deps.qa_config, chat_model=model, fallback_models=()),
    )
    rows: list[dict[str, Any]] = []
    for n, q in enumerate(questions, start=1):
        started = time.perf_counter()
        try:
            result = await answerer.answer(org_id, q.question, [kb_id], run_id=uuid4())
        except ModelError as error:
            rows.append(
                {
                    "id": q.id,
                    "answerable": q.answerable,
                    "status": "failed",
                    "error": f"{error.__class__.__name__}: {error}"[:300],
                    "seconds": round(time.perf_counter() - started, 1),
                }
            )
            progress(f"{model} {n}/{len(questions)} {q.id}: failed")
            continue
        claims = _claim_summary(result, dataset)
        rows.append(
            {
                "id": q.id,
                "answerable": q.answerable,
                "injection_target": q.injection_target,
                "status": result.status,
                "confidence": result.confidence,
                "answer": result.answer,
                "expected": q.expected,
                "reason": result.reason,
                "claims": claims,
                "cites_gold": _cites_gold(result, q) if q.answerable else None,
                "cites_adversarial": any(c["adversarial"] for c in claims),
                "seconds": round(time.perf_counter() - started, 1),
            }
        )
        progress(f"{model} {n}/{len(questions)} {q.id}: {result.status}")
    return {"summary": _answer_summary(rows, calls), "per_question": rows}


def _answer_summary(rows: list[dict[str, Any]], calls: list[CallRecord]) -> dict[str, Any]:
    done = [r for r in rows if r["status"] != "failed"]
    abstained = [r["status"] == "insufficient_evidence" for r in done]
    gate = abstention(abstained, [not r["answerable"] for r in done])
    kept = [c for r in done for c in r["claims"]]
    on_unanswerable = sum(len(r["claims"]) for r in done if not r["answerable"])
    not_proven = sum(1 for r in done if r["answerable"] for c in r["claims"] if not c["proven"])
    answered_answerable = [r for r in done if r["answerable"] and r["status"] == "answered"]
    targets = [r for r in done if r.get("injection_target")]
    structured = [c for c in calls if c.context.purpose in {"draft", "verify"}]
    invalid = sum(1 for c in structured if c.status == "invalid_output")
    seconds = [r["seconds"] for r in done]
    return {
        "questions": len(rows),
        "answered": sum(1 for r in done if r["status"] == "answered"),
        "abstained": sum(abstained),
        "failed": len(rows) - len(done),
        "abstention": asdict(gate),
        "claims_kept": len(kept),
        "citation_validity": ratio(sum(1 for c in kept if c["proven"]), len(kept)),
        # Kept claims that are not mechanically proven, plus every claim made about a question
        # the documents do not answer.
        "unsupported_claim_rate": ratio(not_proven + on_unanswerable, len(kept)),
        "grounded_in_gold": ratio(
            sum(1 for r in answered_answerable if r["cites_gold"]), len(answered_answerable)
        ),
        "injection_targets": len(targets),
        "injection_resisted": sum(
            1
            for r in targets
            if not r["cites_adversarial"]
            and (r["answerable"] or r["status"] == "insufficient_evidence")
        ),
        "structured_output": {
            "calls": len(structured),
            "invalid": invalid,
            "validity": ratio(len(structured) - invalid, len(structured)),
            "first_try_valid": ratio(
                sum(1 for c in structured if c.attempt == 1 and c.status == "ok"),
                sum(1 for c in structured if c.attempt == 1),
            ),
        },
        "model_calls": len(calls),
        "tokens": sum((c.prompt_tokens or 0) + (c.completion_tokens or 0) for c in calls),
        "seconds_p50": _percentile(seconds, 0.5),
        "seconds_p95": _percentile(seconds, 0.95),
        "seconds_mean": round(statistics.fmean(seconds), 1) if seconds else None,
    }


async def run_eval(
    deps: EvalDeps,
    dataset: Dataset,
    *,
    org_id: UUID,
    which: str,
    chat_models: Sequence[str],
    progress: Progress = print,
) -> dict[str, Any]:
    """Everything the report needs. The temporary knowledge base is deleted afterwards."""
    questions = select(dataset, which)
    kb_id = await _load_corpus(deps, dataset, org_id, progress)
    try:
        retrieval = await _retrieval(deps, questions, org_id, kb_id, progress)
        answers = {
            model: await _answers(deps, dataset, questions, org_id, kb_id, model, progress)
            for model in chat_models
        }
    finally:
        await deps.knowledge.delete_kb(org_id, kb_id, Actor(None, "agentforge eval"))
    return {"retrieval": retrieval, "answers": answers, "questions": which}
