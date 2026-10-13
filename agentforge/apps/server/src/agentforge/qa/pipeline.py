"""Grounded answer pipeline (architecture §10.2): retrieve → abstain gate → draft claims with
citations and exact quotes → mechanical checks → LLM verification → keep supported claims.

Reusable: the questionnaire workflow (M4) runs this per item. Nothing here touches the
database except through the retriever and the agent registry.
"""

import time
from collections.abc import Sequence
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
from agentforge.qa.schemas import DraftAnswer, DraftClaim, VerifierOutput

log = get_logger("agentforge.qa")

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
    # distance). Tuned on the Northwind eval set; see docs/evaluation/.
    max_distance: float = 0.5
    verifier_mode: VerifierMode = "low_confidence_only"
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
        )
        for i, h in enumerate(hits, start=1)
    ]


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

    async def answer(
        self,
        org_id: UUID,
        question: str,
        knowledge_base_ids: Sequence[UUID],
        *,
        run_id: UUID | None = None,
    ) -> GroundedAnswer:
        config = self._config
        budget = Budget(
            max_llm_calls=config.max_llm_calls,
            max_tokens=config.max_tokens,
            max_seconds=config.max_seconds,
        )
        timings: dict[str, float] = {}
        started = time.perf_counter()

        budget.step()
        hits = await self._retriever.search(
            org_id, knowledge_base_ids, question, k=config.top_k, run_id=run_id
        )
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
            CallContext(org_id, "draft", run_id),
            build_messages(
                drafter.definition.system_prompt,
                f"Question: {question}",
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
        dropped = len(kept) < len(claims)
        confidence: Confidence = (
            "low"
            if dropped or "partial" in statuses
            else "medium"
            if "rescued" in statuses
            else "high"
        )
        # The answer is exactly the surviving claims: no unverified prose reaches the customer.
        return finish(
            GroundedAnswer(
                "answered",
                " ".join(c.text for c in kept),
                confidence,
                claims,
                evidence,
                versions,
                f"{len(kept)} of {len(claims)} claims verified",
            )
        )

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
            CallContext(org_id, "verify", run_id),
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
