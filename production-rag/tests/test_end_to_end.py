"""
Full pipeline integration test: ingest (fixture JSON) -> chunk -> index ->
hybrid retrieve -> rerank -> generate -> citation enforcement, against the
small fixture corpus. Skips automatically if Ollama isn't reachable.
"""

import json
from pathlib import Path

import pytest

from src.chunk.chunker import chunk_document
from src.generate.generator import RAGGenerator
from src.index.hybrid_index import build_index, get_client
from src.rerank.reranker import CrossEncoderReranker
from src.retrieve.retriever import HybridRetriever

FIXTURES_DIR = Path(__file__).parent / "fixtures"

try:
    import ollama as _ollama
    _ollama.list()
    OLLAMA_AVAILABLE = True
except Exception:
    OLLAMA_AVAILABLE = False

pytestmark = pytest.mark.skipif(not OLLAMA_AVAILABLE, reason="Ollama daemon not reachable")


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    all_chunks = []
    for doc_path in sorted(FIXTURES_DIR.glob("sample_doc*.json")):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        all_chunks.extend(c.to_dict() for c in chunk_document(doc))

    qdrant_path = tmp_path_factory.mktemp("qdrant") / "db"
    client = get_client(qdrant_path)
    build_index(client, all_chunks)

    return {
        "retriever": HybridRetriever(client),
        "reranker": CrossEncoderReranker(),
        "generator": RAGGenerator(run_entailment_check=True),
    }


def _ask(pipeline, question: str, top_n: int = 5):
    candidates = pipeline["retriever"].search(question, mode="hybrid", limit=20)
    reranked = pipeline["reranker"].rerank(question, candidates, top_n=top_n)
    context = [{"chunk_id": r.chunk_id, "text": r.payload["text"]} for r in reranked]
    return pipeline["generator"].answer(question, context)


def test_full_pipeline_never_cites_a_chunk_outside_the_indexed_corpus(pipeline):
    points, _ = pipeline["retriever"].client.scroll(collection_name="cardiology_chunks", limit=1000)
    all_chunk_ids = {p.payload["chunk_id"] for p in points}

    result = _ask(pipeline, "What is the target dose of metoprolol succinate?")
    cited_chunk_ids = {c["chunk_id"] for c in result.citations}
    assert cited_chunk_ids <= all_chunk_ids


def test_full_pipeline_on_a_genuinely_unanswerable_question_refuses(pipeline):
    result = _ask(pipeline, "What is the recommended surgical technique for aortic valve replacement?")
    assert result.status != "answered"
    assert result.citations == []


def test_full_pipeline_result_status_is_always_one_of_the_known_values(pipeline):
    known_statuses = {"answered", "partially_supported", "insufficient_evidence", "uncited_answer", "citation_violation"}
    for question in [
        "What is the starting dose of carvedilol?",
        "What is the loading dose of ticagrelor?",
        "What is the recommended treatment for cardiogenic shock?",
    ]:
        result = _ask(pipeline, question)
        assert result.status in known_statuses
