import json
from pathlib import Path

import pytest

from src.chunk.chunker import chunk_document
from src.index.hybrid_index import build_index, get_client
from src.rerank.reranker import CrossEncoderReranker
from src.retrieve.retriever import HybridRetriever

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_doc.json"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory) -> HybridRetriever:
    doc = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    chunks = [c.to_dict() for c in chunk_document(doc)]
    qdrant_path = tmp_path_factory.mktemp("qdrant") / "db"
    client = get_client(qdrant_path)
    build_index(client, chunks)
    return HybridRetriever(client)


@pytest.fixture(scope="module")
def reranker() -> CrossEncoderReranker:
    return CrossEncoderReranker()


def test_rerank_narrows_to_top_n(retriever, reranker):
    candidates = retriever.search("beta blocker dosing and monitoring", mode="hybrid", limit=4)
    reranked = reranker.rerank("what dose of metoprolol should I start", candidates, top_n=2)
    assert len(reranked) == 2


def test_rerank_promotes_the_most_relevant_chunk_to_first_place(retriever, reranker):
    # deliberately fetch candidates in a possibly-suboptimal retrieval order,
    # and confirm reranking puts the truly best-matching chunk first.
    candidates = retriever.search("metoprolol carvedilol bisoprolol dosing bradycardia", mode="hybrid", limit=4)
    reranked = reranker.rerank("what is the recommended target dose of carvedilol", candidates, top_n=4)
    assert "Carvedilol" in reranked[0].payload["text"]


def test_reranked_results_are_sorted_by_rerank_score_desc(retriever, reranker):
    candidates = retriever.search("figure titration flowchart heart failure", mode="hybrid", limit=4)
    reranked = reranker.rerank("titration flowchart for beta-blockers", candidates, top_n=4)
    scores = [r.rerank_score for r in reranked]
    assert scores == sorted(scores, reverse=True)


def test_rerank_of_empty_candidates_returns_empty(reranker):
    assert reranker.rerank("anything", [], top_n=5) == []
