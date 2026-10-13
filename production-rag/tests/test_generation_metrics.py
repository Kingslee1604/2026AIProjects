from src.eval.generation_metrics import (
    aggregate_generation_metrics,
    citation_precision,
    citation_recall,
    refused_correctly,
)
from src.generate.citation_checker import EntailmentResult
from src.generate.generator import GenerationResult


def _result(status, citations=None, entailment_results=None):
    return GenerationResult(
        status=status,
        answer="some answer",
        citations=citations or [],
        entailment_results=entailment_results or [],
    )


def test_citation_precision_all_supported():
    result = _result(
        "answered",
        entailment_results=[
            EntailmentResult("c1", "claim1", True, "ok"),
            EntailmentResult("c2", "claim2", True, "ok"),
        ],
    )
    assert citation_precision(result) == 1.0


def test_citation_precision_partial():
    result = _result(
        "partially_supported",
        entailment_results=[
            EntailmentResult("c1", "claim1", True, "ok"),
            EntailmentResult("c2", "claim2", False, "not really"),
        ],
    )
    assert citation_precision(result) == 0.5


def test_citation_precision_none_when_no_entailment_ran():
    result = _result("answered", entailment_results=[])
    assert citation_precision(result) is None


def test_citation_recall_full_and_partial():
    result = _result("answered", citations=[{"chunk_id": "c1"}, {"chunk_id": "c2"}])
    assert citation_recall(result, {"c1", "c2"}) == 1.0
    assert citation_recall(result, {"c1", "c2", "c3"}) == 2 / 3


def test_citation_recall_none_when_no_expected_citations():
    result = _result("insufficient_evidence", citations=[])
    assert citation_recall(result, set()) is None


def test_refused_correctly_on_unanswerable_question():
    refusal = _result("insufficient_evidence")
    hallucination = _result("answered", citations=[{"chunk_id": "c1"}])
    assert refused_correctly(refusal, is_answerable=False) is True
    assert refused_correctly(hallucination, is_answerable=False) is False


def test_refused_correctly_on_answerable_question():
    answered = _result("answered", citations=[{"chunk_id": "c1"}])
    wrongly_refused = _result("insufficient_evidence")
    assert refused_correctly(answered, is_answerable=True) is True
    assert refused_correctly(wrongly_refused, is_answerable=True) is False


def test_aggregate_generation_metrics():
    rows = [
        {"citation_precision": 1.0, "citation_recall": 1.0, "refused_correctly": True, "answer_relevant": True},
        {"citation_precision": 0.5, "citation_recall": None, "refused_correctly": False, "answer_relevant": True},
    ]
    agg = aggregate_generation_metrics(rows)
    assert agg["n"] == 2
    assert agg["citation_precision"] == 0.75
    assert agg["citation_recall"] == 1.0  # only one row had a non-None value
    assert agg["refusal_accuracy"] == 0.5
    assert agg["answer_relevance"] == 1.0


def test_aggregate_generation_metrics_all_none_fields():
    rows = [{"citation_precision": None, "citation_recall": None, "refused_correctly": True}]
    agg = aggregate_generation_metrics(rows)
    assert agg["citation_precision"] is None
    assert agg["answer_relevance"] is None
