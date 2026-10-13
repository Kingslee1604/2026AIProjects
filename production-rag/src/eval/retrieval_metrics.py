"""
Retrieval-only metrics for the ablation eval (BM25-only vs vector-only vs
hybrid vs hybrid+rerank). Pure functions, binary relevance, no external deps.
"""

from __future__ import annotations

import math


def hit_rate_at_k(ranked_chunk_ids: list[str], relevant_chunk_ids: set[str], k: int) -> float:
    return 1.0 if set(ranked_chunk_ids[:k]) & relevant_chunk_ids else 0.0


def recall_at_k(ranked_chunk_ids: list[str], relevant_chunk_ids: set[str], k: int) -> float:
    if not relevant_chunk_ids:
        return 0.0
    hits = set(ranked_chunk_ids[:k]) & relevant_chunk_ids
    return len(hits) / len(relevant_chunk_ids)


def reciprocal_rank(ranked_chunk_ids: list[str], relevant_chunk_ids: set[str]) -> float:
    for i, cid in enumerate(ranked_chunk_ids, start=1):
        if cid in relevant_chunk_ids:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked_chunk_ids: list[str], relevant_chunk_ids: set[str], k: int) -> float:
    """Binary-relevance nDCG@k."""
    dcg = 0.0
    for i, cid in enumerate(ranked_chunk_ids[:k], start=1):
        if cid in relevant_chunk_ids:
            dcg += 1.0 / math.log2(i + 1)

    ideal_hits = min(len(relevant_chunk_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def aggregate_metrics(
    per_query_ranked_ids: list[list[str]],
    per_query_relevant_ids: list[set[str]],
    k: int = 5,
) -> dict[str, float]:
    assert len(per_query_ranked_ids) == len(per_query_relevant_ids)
    n = len(per_query_ranked_ids)
    if n == 0:
        return {"hit_rate@k": 0.0, "recall@k": 0.0, "mrr": 0.0, "ndcg@k": 0.0, "n": 0}

    hit_rates, recalls, rrs, ndcgs = [], [], [], []
    for ranked, relevant in zip(per_query_ranked_ids, per_query_relevant_ids):
        hit_rates.append(hit_rate_at_k(ranked, relevant, k))
        recalls.append(recall_at_k(ranked, relevant, k))
        rrs.append(reciprocal_rank(ranked, relevant))
        ndcgs.append(ndcg_at_k(ranked, relevant, k))

    return {
        "hit_rate@k": sum(hit_rates) / n,
        "recall@k": sum(recalls) / n,
        "mrr": sum(rrs) / n,
        "ndcg@k": sum(ndcgs) / n,
        "n": n,
    }
