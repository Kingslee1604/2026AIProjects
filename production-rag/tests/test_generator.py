"""
Integration tests against a real local Ollama daemon (model: qwen2.5:7b, the
default set in src/generate/generator.py -- see README "Engineering notes"
for why llama3.2:3b was replaced: 0.19 citation precision vs 0.88).
Skipped automatically if Ollama isn't reachable, so the rest of the suite
still runs in environments without it (e.g. CI's fast smoke job).
"""

import pytest

import src.generate.generator as generator_module
from src.generate.generator import RAGGenerator

pytest.importorskip("ollama")

try:
    import ollama as _ollama
    _ollama.list()
    OLLAMA_AVAILABLE = True
except Exception:
    OLLAMA_AVAILABLE = False

pytestmark = pytest.mark.skipif(not OLLAMA_AVAILABLE, reason="Ollama daemon not reachable")

CONTEXT = [
    {
        "chunk_id": "fixture-doc-1__chunk-1",
        "text": (
            "Table (Table 1. Recommended starting and target doses):\n"
            "| Drug | Starting dose | Target dose |\n"
            "| --- | --- | --- |\n"
            "| Metoprolol succinate | 12.5-25 mg once daily | 200 mg once daily |\n"
            "| Carvedilol | 3.125 mg twice daily | 25-50 mg twice daily |\n"
            "| Bisoprolol | 1.25 mg once daily | 10 mg once daily |"
        ),
    },
    {
        "chunk_id": "fixture-doc-1__chunk-3",
        "text": "Patients should be monitored for bradycardia and hypotension during up-titration.",
    },
]


def test_answered_status_always_implies_nonempty_grounded_citations():
    """Small local models don't reliably include citations on every call even
    at temperature 0 (observed directly with llama3.2:3b: identical question
    sometimes returned status='uncited_answer' -- see README "Engineering
    notes"). So this test doesn't pin one call to one status -- it asserts
    the contract that must hold no matter which status comes back:
    'answered'/'partially_supported' NEVER ships with missing or ungrounded
    citations, and every other status carries the safe fallback."""
    gen = RAGGenerator(run_entailment_check=False)
    retrieved_ids = {c["chunk_id"] for c in CONTEXT}

    result = gen.answer("What is the target dose of bisoprolol?", CONTEXT)

    if result.status in ("answered", "partially_supported"):
        assert result.citations
        assert all(c["chunk_id"] in retrieved_ids for c in result.citations)
    else:
        assert result.answer == generator_module.FALLBACK_MESSAGE
        assert result.citations == []


def test_unanswerable_question_triggers_insufficient_evidence_fallback():
    gen = RAGGenerator(run_entailment_check=False)
    result = gen.answer(
        "What is the recommended surgical approach for aortic dissection repair?", CONTEXT
    )
    assert result.status == "insufficient_evidence"
    assert result.citations == []


def test_entailment_check_runs_and_returns_a_verdict_per_citation():
    gen = RAGGenerator(run_entailment_check=True)
    result = gen.answer("What dose of bisoprolol should a patient be started on?", CONTEXT)
    if result.status in ("answered", "partially_supported"):
        assert len(result.entailment_results) >= 1


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeResponse:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


def test_answer_with_zero_citations_is_not_reported_as_answered(monkeypatch):
    """Regression test: a model can produce insufficient_evidence=false with an
    empty citations list (it made claims but cited nothing). That must never
    surface as a trustworthy 'answered' result -- caught this via a real
    Ollama call during development where status came back 'answered' with
    citations=[]."""
    import src.generate.generator as generator_module

    uncited_json = '{"answer": "Some claim with no source.", "citations": [], "insufficient_evidence": false}'
    monkeypatch.setattr(
        generator_module.ollama, "chat", lambda **kwargs: _FakeResponse(uncited_json)
    )

    gen = RAGGenerator(run_entailment_check=False)
    result = gen.answer("irrelevant question", CONTEXT)

    assert result.status != "answered"
    assert result.status == "uncited_answer"
    assert result.answer == generator_module.FALLBACK_MESSAGE
