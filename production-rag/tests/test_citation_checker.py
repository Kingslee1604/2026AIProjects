from src.generate.citation_checker import grounding_check
from src.generate.schemas import Citation


def test_grounding_check_passes_when_all_citations_are_retrieved():
    citations = [
        Citation(chunk_id="doc1__chunk-0", quote="q1", claim="c1"),
        Citation(chunk_id="doc1__chunk-2", quote="q2", claim="c2"),
    ]
    retrieved = {"doc1__chunk-0", "doc1__chunk-1", "doc1__chunk-2"}
    assert grounding_check(citations, retrieved) == []


def test_grounding_check_flags_a_hallucinated_chunk_id():
    citations = [
        Citation(chunk_id="doc1__chunk-0", quote="q1", claim="c1"),
        Citation(chunk_id="doc1__chunk-99", quote="q2", claim="c2"),  # never retrieved
    ]
    retrieved = {"doc1__chunk-0", "doc1__chunk-1"}
    assert grounding_check(citations, retrieved) == ["doc1__chunk-99"]


def test_grounding_check_flags_all_citations_from_a_different_document_entirely():
    citations = [Citation(chunk_id="wrong-doc__chunk-0", quote="q", claim="c")]
    retrieved = {"doc1__chunk-0"}
    assert grounding_check(citations, retrieved) == ["wrong-doc__chunk-0"]


def test_grounding_check_on_no_citations_returns_no_violations():
    assert grounding_check([], {"doc1__chunk-0"}) == []
