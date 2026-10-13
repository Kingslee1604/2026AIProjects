"""
Query-time retrieval: bm25-only, vector-only, or hybrid (RRF fusion of both).

The three modes exist as a first-class parameter (not just an implementation
detail) because the retrieval ablation eval (src/eval/) compares them directly
to show the measured lift from hybrid retrieval -- see plan's ablation table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from qdrant_client import QdrantClient, models

from src.index.hybrid_index import COLLECTION_NAME, DENSE_MODEL, SPARSE_MODEL, vector_field_names

RetrievalMode = Literal["bm25", "vector", "hybrid"]


@dataclass
class RetrievedChunk:
    chunk_id: str
    score: float
    payload: dict


class HybridRetriever:
    def __init__(self, client: QdrantClient):
        self.client = client
        self.dense_name, self.sparse_name = vector_field_names(client)

    def search(self, query_text: str, mode: RetrievalMode = "hybrid", limit: int = 10) -> list[RetrievedChunk]:
        if mode == "vector":
            result = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=models.Document(text=query_text, model=DENSE_MODEL),
                using=self.dense_name,
                limit=limit,
            )
        elif mode == "bm25":
            result = self.client.query_points(
                collection_name=COLLECTION_NAME,
                query=models.Document(text=query_text, model=SPARSE_MODEL),
                using=self.sparse_name,
                limit=limit,
            )
        elif mode == "hybrid":
            result = self.client.query_points(
                collection_name=COLLECTION_NAME,
                prefetch=[
                    models.Prefetch(
                        query=models.Document(text=query_text, model=DENSE_MODEL),
                        using=self.dense_name,
                        limit=max(limit * 4, 20),
                    ),
                    models.Prefetch(
                        query=models.Document(text=query_text, model=SPARSE_MODEL),
                        using=self.sparse_name,
                        limit=max(limit * 4, 20),
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
            )
        else:
            raise ValueError(f"Unknown retrieval mode: {mode}")

        return [
            RetrievedChunk(chunk_id=p.payload["chunk_id"], score=p.score, payload=p.payload)
            for p in result.points
        ]
