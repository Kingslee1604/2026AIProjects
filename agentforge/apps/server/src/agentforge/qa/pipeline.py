"""Grounded answer pipeline (architecture §10.2): retrieve → abstain gate → draft claims with
citations and exact quotes → mechanical checks → LLM verification → keep supported claims.

Reusable: the questionnaire workflow (M4) runs this per item. Nothing here touches the
database except through the retriever and the agent registry.
"""

import time
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import UUID

from agentforge.agents.budget import Budget
from agentforge.agents.prompts import EvidencePassage, build_messages
from agentforge.agents.registry import AgentDefinition, AgentRegistry
from agentforge.core.logging import get_logger
from agentforge.knowledge.retrieval import Retriever, SearchHit
from agentforge.models import CallContext, ModelGateway
from agentforge.qa.matching import (
    MIN_CLAIM_OVERLAP,
    claim_overlap,
    is_non_answer,
    looks_truncated,
    numbers_supported,
    quote_found,
)
from agentforge.qa.schemas import DraftAnswer, DraftClaim, RelevanceOutput, VerifierOutput

log = get_logger("agentforge.qa")

# The run step whose model calls these are (trace view). A context variable, not an
# attribute: one answerer serves many items concurrently, each in its own asyncio task.
_STEP: ContextVar[UUID | None] = ContextVar("agentforge_qa_step", default=None)
SEARCH_RRF_K = 60

INSUFFICIENT = "Insufficient evidence in the knowledge base."
RESTATEMENT_OVERLAP = 0.75  # share of the quote's words in the claim: a restatement
VerifierMode = Literal["all", "low_confidence_only", "off"]
ClaimStatus = Literal["verified", "rescued", "partial", "dropped"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class QAConfig:
    chat_model: str
    fallback_models: Sequence[str] = ()
    top_k: int = 6
    # Abstain without calling the drafter when no passage is at least this close (cosine
    # distance). Measured on the Northwind eval set; see docs/evaluation/.
    max_distance: float = 0.5
    verifier_mode: VerifierMode = "low_confidence_only"
    # One model call per answered question: does the verified answer respond to the question?
    # Catches true-but-off-topic answers, which pass every check on the quotes.
    relevance_check: bool = True
    max_llm_calls: int = 12
    max_tokens: int = 40_000
    max_seconds: float = 900.0


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    chunk_id: str
    document_id: str
    filename: str
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: list[str]
    content: str
    score: float
    vector_distance: float | None
    flagged: bool = False  # from a document that tries to steer an AI (knowledge/screening)


@dataclass(slots=True)
class Claim:
    text: str
    citations: list[str]
    quote: str
    citations_valid: bool
    quote_found: bool
    # Most of the claim's words come from the quote, its numbers appear in the cited
    # passages, and it is not truncated.
    quote_supports_claim: bool
    drafted_text: str  # what the model wrote; `text` may be the verbatim quote instead
    verdict: str | None = None  # LLM verifier verdict, when it ran
    reason: str | None = None
    status: ClaimStatus = "dropped"
    duplicate: bool = False  # left out because an earlier sentence says the same
    flagged_source: bool = False  # cites a passage from a flagged document: check before use

    @property
    def kept(self) -> bool:
        return self.status != "dropped"

    @property
    def proven(self) -> bool:
        """Mechanically proven: a real quote from a cited passage that says what the claim says."""
        return self.citations_valid and self.quote_found and self.quote_supports_claim


@dataclass(slots=True)
class GroundedAnswer:
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    confidence: Confidence | None
    claims: list[Claim]
    evidence: list[Evidence]
    agent_versions: dict[str, str]
    reason: str  # why the outcome is what it is, for the trace and the reviewer
    timings: dict[str, float] = field(default_factory=lambda: {})
    budget: dict[str, float] = field(default_factory=lambda: {})

    def claims_json(self) -> list[dict[str, object]]:
        return [{**asdict(c), "kept": c.kept} for c in self.claims]

    def evidence_json(self) -> list[dict[str, object]]:
        return [asdict(e) for e in self.evidence]


def _evidence(hits: Sequence[SearchHit]) -> list[Evidence]:
    return [
        Evidence(
            id=f"E{i}",
            chunk_id=str(h.chunk_id),
            document_id=str(h.document_id),
            filename=h.filename,
            page_start=h.page_start,
            page_end=h.page_end,
            char_start=h.char_start,
            char_end=h.char_end,
            section_path=h.section_path,
            content=h.content,
            score=h.score,
            vector_distance=h.vector_distance,
            flagged=h.flagged,
        )
        for i, h in enumerate(hits, start=1)
    ]


def _task(question: str, parts: Sequence[str]) -> str:
    if not parts:
        return f"Question: {question}"
    listed = "\n".join(f"- {part}" for part in parts)
    return (
        f"Question: {question}\n\nThis question has several parts. Answer each part with its own "
        f"claims, and leave out any part the evidence does not answer:\n{listed}"
    )


def _passage(e: Evidence) -> EvidencePassage:
    pages = str(e.page_start) if e.page_start == e.page_end else f"{e.page_start}-{e.page_end}"
    return EvidencePassage(e.id, e.filename, pages, " / ".join(e.section_path), e.content)


def mechanical_check(draft: DraftClaim, evidence: dict[str, Evidence]) -> Claim:
    """Cited ids must be among the passages shown; the quote must appear in a cited passage
    and say what the claim says.

    When the claim merely restates its quote, the verbatim quote becomes the claim text:
    evidence wording is safer than a small model's rewording ("AES-..." for "AES-256").
    """
    citations = list(dict.fromkeys(c.strip().upper() for c in draft.citations))
    valid = bool(citations) and all(c in evidence for c in citations)
    cited = [evidence[c].content for c in citations if c in evidence]
    drafted, quote = draft.text.strip(), " ".join(draft.quote.split())
    found = valid and quote_found(quote, cited)
    text = drafted
    if (
        found
        and claim_overlap(drafted, quote) >= MIN_CLAIM_OVERLAP
        and claim_overlap(quote, drafted) >= RESTATEMENT_OVERLAP
    ):
        text = quote
    supports = (
        claim_overlap(text, quote) >= MIN_CLAIM_OVERLAP
        and numbers_supported(text, cited)
        and not looks_truncated(text)
    )
    return Claim(
        text=text,
        citations=citations,
        quote=quote,
        citations_valid=valid,
        quote_found=found,
        quote_supports_claim=supports,
        drafted_text=drafted,
    )


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def drop_repeats(claims: Sequence[Claim]) -> None:
    """Leave out a kept claim that says nothing new: the same sentence as an earlier one, or
    the same quote as an earlier claim that already shows that whole quote. (A claim that
    quotes the same passage for a different part of the question is kept.)"""
    texts: set[str] = set()
    shown_quotes: set[str] = set()
    for claim in claims:
        if not claim.kept:
            continue
        text, quote = _key(claim.text), _key(claim.quote)
        if text in texts or quote in shown_quotes:
            claim.status, claim.duplicate = "dropped", True
            claim.reason = "repeats an earlier sentence"
            continue
        texts.add(text)
        if text == quote:
            shown_quotes.add(quote)


class GroundedAnswerer:
    def __init__(
        self,
        gateway: ModelGateway,
        retriever: Retriever,
        registry: AgentRegistry,
        config: QAConfig,
    ) -> None:
        self._gateway = gateway
        self._retriever = retriever
        self._registry = registry
        self._config = config

    def _models(self, definition: AgentDefinition) -> tuple[str, list[str]]:
        """The agent's model chain, trimmed so first try + repair per model fits its limit."""
        model = self._config.chat_model if definition.model == "default" else definition.model
        fallbacks = list(definition.fallbacks or self._config.fallback_models)
        max_models = max(definition.limits.max_llm_calls // 2, 1)
        return model, fallbacks[: max_models - 1]

    async def _search(
        self,
        org_id: UUID,
        knowledge_base_ids: Sequence[UUID],
        queries: Sequence[str],
        run_id: UUID | None,
    ) -> list[SearchHit]:
        """Search each distinct query and fuse the rankings (RRF), keeping each passage once
        with its closest vector distance. One query: the retriever's own ranking."""
        distinct = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        k = self._config.top_k
        if len(distinct) <= 1:
            query = distinct[0] if distinct else ""
            return await self._retriever.search(
                org_id, knowledge_base_ids, query, k=k, run_id=run_id
            )
        scores: dict[UUID, float] = {}
        best: dict[UUID, SearchHit] = {}
        for query in distinct:
            hits = await self._retriever.search(
                org_id, knowledge_base_ids, query, k=k, run_id=run_id
            )
            for rank, hit in enumerate(hits, start=1):
                scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (SEARCH_RRF_K + rank)
                kept = best.get(hit.chunk_id)
                if kept is None or (hit.vector_distance or 9.0) < (kept.vector_distance or 9.0):
                    best[hit.chunk_id] = hit
        ranked = sorted(scores, key=lambda c: (-scores[c], str(c)))[:k]
        return [best[c] for c in ranked]

    async def answer(
        self,
        org_id: UUID,
        question: str,
        knowledge_base_ids: Sequence[UUID],
        *,
        run_id: UUID | None = None,
        step_id: UUID | None = None,
        search_queries: Sequence[str] = (),
        parts: Sequence[str] = (),
    ) -> GroundedAnswer:
        """Answer one question. `search_queries` (from the planner) are searched in addition to
        the question; `parts` tell the drafter to answer each part of a compound question."""
        config = self._config
        budget = Budget(
            max_llm_calls=config.max_llm_calls,
            max_tokens=config.max_tokens,
            max_seconds=config.max_seconds,
        )
        timings: dict[str, float] = {}
        started = time.perf_counter()
        _STEP.set(step_id)

        budget.step()
        hits = await self._search(org_id, knowledge_base_ids, [question, *search_queries], run_id)
        timings["retrieve_ms"] = round((time.perf_counter() - started) * 1000, 1)
        evidence = _evidence(hits)

        def finish(result: GroundedAnswer) -> GroundedAnswer:
            result.timings = {
                **timings,
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            result.budget = budget.snapshot()
            log.info(
                "answer_finished",
                run_id=str(run_id) if run_id else None,
                status=result.status,
                claims=sum(c.kept for c in result.claims),
                **result.timings,
            )
            return result

        best = min(
            (e.vector_distance for e in evidence if e.vector_distance is not None), default=None
        )
        if best is None or best > config.max_distance:
            return finish(
                GroundedAnswer(
                    "insufficient_evidence",
                    INSUFFICIENT,
                    None,
                    [],
                    evidence,
                    {},
                    f"no passage within distance {config.max_distance} (best: {best})",
                )
            )

        drafter = await self._registry.version(org_id, "drafter")
        versions = {"drafter": str(drafter.id)}
        model, fallbacks = self._models(drafter.definition)
        budget.step()
        budget.check()
        t0 = time.perf_counter()
        draft, result = await self._gateway.generate_structured(
            CallContext(org_id, "draft", run_id, _STEP.get()),
            build_messages(
                drafter.definition.system_prompt,
                _task(question, parts),
                [_passage(e) for e in evidence],
            ),
            DraftAnswer,
            model=model,
            fallbacks=fallbacks,
            temperature=drafter.definition.temperature,
            max_tokens=drafter.definition.max_tokens,
        )
        budget.charge(result)
        timings["draft_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        if draft.insufficient_evidence or not draft.claims:
            return finish(
                GroundedAnswer(
                    "insufficient_evidence",
                    INSUFFICIENT,
                    None,
                    [],
                    evidence,
                    versions,
                    "the drafter found no answer in the evidence",
                )
            )

        by_id = {e.id: e for e in evidence}
        claims = [mechanical_check(c, by_id) for c in draft.claims]
        t0 = time.perf_counter()
        await self._verify(org_id, run_id, question, claims, by_id, budget, versions)
        timings["verify_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        drop_repeats(claims)
        kept = [c for c in claims if c.kept]
        if not kept:
            only_abstentions = all(is_non_answer(c.text) for c in claims)
            return finish(
                GroundedAnswer(
                    "insufficient_evidence",
                    INSUFFICIENT,
                    None,
                    claims,
                    evidence,
                    versions,
                    "the drafter found no answer in the evidence"
                    if only_abstentions
                    else "no drafted claim passed verification",
                )
            )
        statuses = {c.status for c in kept}
        # A repeated sentence was left out, not refuted: it does not lower confidence.
        dropped = any(not c.kept and not c.duplicate for c in claims)
        confidence: Confidence = (
            "low"
            if dropped or "partial" in statuses
            else "medium"
            if "rescued" in statuses
            else "high"
        )
        # The answer is exactly the surviving claims: no unverified prose reaches the customer.
        answer = " ".join(c.text for c in kept)
        drafted = sum(1 for c in claims if not c.duplicate)
        reason = f"{len(kept)} of {drafted} claims verified"

        # Quoted from a document that tries to steer an AI: true to the document, but the
        # document itself may be planted. A person checks it before it is used.
        for claim in claims:
            claim.flagged_source = any(by_id[c].flagged for c in claim.citations if c in by_id)
        suspicious = sorted(
            {
                by_id[c].filename
                for k in kept
                for c in k.citations
                if c in by_id and by_id[c].flagged
            }
        )
        if suspicious:
            confidence = "low"
            reason += (
                "; cites a document flagged as suspicious (it contains instructions aimed at "
                f"AI assistants): {', '.join(suspicious)}"
            )

        if config.relevance_check:
            t0 = time.perf_counter()
            relevance = await self._check_relevance(
                org_id, run_id, question, answer, budget, versions
            )
            timings["relevance_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if relevance is None:
                reason += "; relevance not checked (model-call budget spent)"
            elif relevance.relevance == "does_not_answer":
                # True sentences about something else: say so, rather than answer off-topic.
                return finish(
                    GroundedAnswer(
                        "insufficient_evidence",
                        INSUFFICIENT,
                        None,
                        claims,
                        evidence,
                        versions,
                        f"the passages found do not answer this question: {relevance.reason}",
                    )
                )
            elif relevance.relevance == "partially_answers":
                confidence = "low"
                reason += f"; answers only part of the question: {relevance.reason}"

        return finish(
            GroundedAnswer("answered", answer, confidence, claims, evidence, versions, reason)
        )

    async def _check_relevance(
        self,
        org_id: UUID,
        run_id: UUID | None,
        question: str,
        answer: str,
        budget: Budget,
        versions: dict[str, str],
    ) -> RelevanceOutput | None:
        """Does the verified answer respond to the question? None when the budget is spent."""
        if budget.remaining_llm_calls() < 2:
            return None
        checker = await self._registry.version(org_id, "relevance")
        versions["relevance"] = str(checker.id)
        model, fallbacks = self._models(checker.definition)
        budget.step()
        output, result = await self._gateway.generate_structured(
            CallContext(org_id, "relevance", run_id, _STEP.get()),
            build_messages(
                checker.definition.system_prompt,
                f"Question: {question}",
                [EvidencePassage("A1", "answer", "", "", answer)],
            ),
            RelevanceOutput,
            model=model,
            fallbacks=fallbacks,
            temperature=checker.definition.temperature,
            max_tokens=checker.definition.max_tokens,
        )
        budget.charge(result)
        return output

    async def _verify(
        self,
        org_id: UUID,
        run_id: UUID | None,
        question: str,
        claims: list[Claim],
        evidence: dict[str, Evidence],
        budget: Budget,
        versions: dict[str, str],
    ) -> None:
        """Decide each claim's status.

        - "The evidence does not mention X": dropped; it is an abstention, not a fact.
        - Bad citation ids: dropped, never sent to a model (fabricated citation).
        - Proven (quote found in a cited passage and matching the claim): verified
          mechanically. In mode `all`, the LLM must also agree.
        - Otherwise low confidence: the LLM may rescue it (supported) unless mode is off.
        When the budget runs out, unchecked claims fall back to the mechanical result.
        """
        mode = self._config.verifier_mode
        for claim in claims:
            if is_non_answer(claim.text):
                claim.status, claim.reason = "dropped", "says the evidence does not answer"
                continue
            if not claim.citations_valid:
                claim.status, claim.reason = "dropped", "cites evidence that was not provided"
                continue
            needs_llm = mode == "all" or (mode == "low_confidence_only" and not claim.proven)
            if needs_llm and budget.remaining_llm_calls() >= 2:
                verdict = await self._verify_one(
                    org_id, run_id, question, claim, evidence, budget, versions
                )
                claim.verdict, claim.reason = verdict.verdict, verdict.reason
                if verdict.verdict == "not_supported":
                    claim.status = "dropped"
                elif verdict.verdict == "partially_supported":
                    claim.status = "partial"
                else:
                    claim.status = "verified" if claim.proven else "rescued"
            elif claim.proven:
                claim.status = "verified"
            else:
                claim.status = "dropped"
                claim.reason = (
                    "quote does not support the claim"
                    if claim.quote_found
                    else "quote not found in the cited passage"
                )

    async def _verify_one(
        self,
        org_id: UUID,
        run_id: UUID | None,
        question: str,
        claim: Claim,
        evidence: dict[str, Evidence],
        budget: Budget,
        versions: dict[str, str],
    ) -> VerifierOutput:
        verifier = await self._registry.version(org_id, "verifier")
        versions["verifier"] = str(verifier.id)
        model, fallbacks = self._models(verifier.definition)
        budget.step()
        task = f"Question: {question}\nClaim: {claim.text}"
        output, result = await self._gateway.generate_structured(
            CallContext(org_id, "verify", run_id, _STEP.get()),
            build_messages(
                verifier.definition.system_prompt,
                task,
                [_passage(evidence[c]) for c in claim.citations],
            ),
            VerifierOutput,
            model=model,
            fallbacks=fallbacks,
            temperature=verifier.definition.temperature,
            max_tokens=verifier.definition.max_tokens,
        )
        budget.charge(result)
        return output
