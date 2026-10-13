import json
from pathlib import Path

import pytest

from src.chunk.chunker import chunk_document
from src.index.hybrid_index import build_index, get_client
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


def test_hybrid_search_returns_the_table_for_a_dosing_question(retriever):
    results = retriever.search("what is the target dose of metoprolol", mode="hybrid", limit=3)
    assert results
    assert results[0].payload["chunk_type"] == "table"
    assert "Metoprolol" in results[0].payload["text"]


def test_hybrid_search_returns_the_right_text_chunk_for_a_monitoring_question(retriever):
    results = retriever.search("what side effects should be monitored during titration", mode="hybrid", limit=3)
    assert results
    assert "bradycardia" in results[0].payload["text"]


def test_all_three_retrieval_modes_run_without_error(retriever):
    for mode in ("bm25", "vector", "hybrid"):
        results = retriever.search("beta blocker heart failure dosing", mode=mode, limit=3)
        assert len(results) > 0
        assert all(isinstance(r.score, float) for r in results)


def test_scores_are_sorted_descending(retriever):
    results = retriever.search("figure titration flowchart", mode="hybrid", limit=4)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
