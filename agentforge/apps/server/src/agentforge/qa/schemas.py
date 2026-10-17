"""Structured outputs the models must produce (validated by the gateway, repaired once)."""

from typing import Literal

from pydantic import BaseModel, Field


class DraftClaim(BaseModel):
    text: str = Field(min_length=1, max_length=600, description="One short factual sentence.")
    citations: list[str] = Field(
        max_length=5, description='Ids of supporting evidence passages, e.g. ["E2"].'
    )
    quote: str = Field(
        max_length=600, description="An exact, word-for-word quote from a cited passage."
    )


class DraftAnswer(BaseModel):
    """No free-text answer field: the verified claims, joined, are the answer."""

    insufficient_evidence: bool
    claims: list[DraftClaim] = Field(max_length=8)


Verdict = Literal["supported", "partially_supported", "not_supported"]


class VerifierOutput(BaseModel):
    verdict: Verdict
    reason: str = Field(max_length=400)


Relevance = Literal["answers", "partially_answers", "does_not_answer"]


class RelevanceOutput(BaseModel):
    """Does the (already verified) answer respond to what the question asks?"""

    relevance: Relevance
    reason: str = Field(max_length=400)
