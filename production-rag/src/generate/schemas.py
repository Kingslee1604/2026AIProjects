from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: str
    quote: str
    claim: str  # the specific part of the answer this citation supports


class CitedAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    insufficient_evidence: bool


class EntailmentVerdict(BaseModel):
    supported: bool
    reasoning: str


class RelevanceVerdict(BaseModel):
    relevant: bool
    reasoning: str
