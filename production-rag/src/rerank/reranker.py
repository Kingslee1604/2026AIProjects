"""
Cross-encoder reranking over the hybrid retriever's fused candidates.

Default model is cross-encoder/ms-marco-MiniLM-L-6-v2 (22M params) so a rerank
pass over ~20-30 candidates stays fast on a CPU-only laptop. See the plan for
the documented GPU upgrade path (bge-reranker-v2-m3) -- swap the model name,
nothing else in the pipeline changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentence_transformers import CrossEncoder

from src.retrieve.retriever import RetrievedChunk

DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@dataclass
class RerankedChunk:
    chunk_id: str
    rerank_score: float
    retrieval_score: float
    payload: dict


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL):
        self.model = CrossEncoder(model_name)

    def rerank(self, query_text: str, candidates: list[RetrievedChunk], top_n: int = 5) -> list[RerankedChunk]:
        if not candidates:
            return []
        pairs = [(query_text, c.payload["text"]) for c in candidates]
        scores = self.model.predict(pairs)
        reranked = [
            RerankedChunk(
                chunk_id=c.chunk_id,
                rerank_score=float(score),
                retrieval_score=c.score,
                payload=c.payload,
            )
            for c, score in zip(candidates, scores)
        ]
        reranked.sort(key=lambda r: r.rerank_score, reverse=True)
        return reranked[:top_n]
