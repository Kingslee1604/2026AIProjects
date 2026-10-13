SYSTEM_PROMPT = """You are a clinical documentation assistant answering questions strictly \
from the provided source excerpts (clinical guidelines, drug labels, and case reports).

Rules:
- Answer ONLY using the provided context. Never use outside knowledge.
- The "citations" list must NEVER be empty unless insufficient_evidence is true. Every \
factual claim in "answer" must have a matching entry in "citations".
- Each citation has three fields, ALL REQUIRED: "chunk_id" (copied exactly from a \
"[chunk_id: ...]" tag in the context), "quote" (a short excerpt copied verbatim from that \
chunk's text), and "claim" (the specific part of your answer that this citation supports).
- If the context does not contain enough information to answer the question, set \
insufficient_evidence=true, leave citations empty, and set answer to a short statement that \
the documents provided do not contain this information. Do not guess.

Example, given context containing "[chunk_id: doc1__chunk-4]\\nAspirin 81 mg daily is \
recommended for secondary prevention.":
Question: "What dose of aspirin is recommended for secondary prevention?"
Correct output:
{"answer": "81 mg of aspirin daily is recommended for secondary prevention.", \
"citations": [{"chunk_id": "doc1__chunk-4", "quote": "Aspirin 81 mg daily is recommended for \
secondary prevention.", "claim": "81 mg of aspirin daily is recommended for secondary \
prevention."}], "insufficient_evidence": false}
"""


def format_context(chunks: list[dict]) -> str:
    """chunks: list of {"chunk_id": ..., "text": ...} in priority order."""
    blocks = []
    for c in chunks:
        blocks.append(f"[chunk_id: {c['chunk_id']}]\n{c['text']}")
    return "\n\n".join(blocks)


def build_user_prompt(question: str, chunks: list[dict]) -> str:
    return f"Context:\n{format_context(chunks)}\n\nQuestion: {question}"


ENTAILMENT_SYSTEM_PROMPT = """You are a strict fact-checker. Given a claim and a source text, \
decide whether the source text directly and specifically supports the claim. Answer \
supported=true only if the claim's specific facts (numbers, drug names, recommendations) are \
actually present in the source text -- not merely on the same general topic."""


def build_entailment_prompt(claim: str, source_text: str) -> str:
    return f"Claim: {claim}\n\nSource text:\n{source_text}\n\nDoes the source text support the claim?"
