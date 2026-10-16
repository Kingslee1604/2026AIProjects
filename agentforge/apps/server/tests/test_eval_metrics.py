"""Evaluation metric definitions (R1-40): small hand-checked cases."""

from agentforge.evaluation.dataset import GoldEvidence, contains_quote, same_document
from agentforge.evaluation.metrics import (
    Passage,
    abstention,
    all_found_at,
    gold_ranks,
    mrr,
    overlap,
    recall_at,
    sweep,
)

LOGS = GoldEvidence("04-logging-and-monitoring-standard.md", "retained for ninety days")
KEYS = GoldEvidence("03-encryption-standard.md", "rotated every twelve months")


def test_quotes_match_across_case_whitespace_and_built_formats() -> None:
    assert contains_quote("Access logs are RETAINED  for\nninety days.", "retained for ninety days")
    assert not contains_quote("Access logs are retained for 90 days.", "retained for ninety days")
    assert same_document("03-encryption-standard.docx", "03-encryption-standard.md")
    assert not same_document("02-access-control-standard.md", "03-encryption-standard.md")


def test_a_quote_only_counts_in_its_own_document() -> None:
    passages = [
        Passage("faq-draft.md", "Logs are retained for ninety days."),  # right words, wrong file
        Passage("01-information-security-policy.md", "Unrelated."),
        Passage("04-logging-and-monitoring-standard.md", "Logs are retained for ninety days."),
    ]
    assert gold_ranks(passages, [LOGS, KEYS]) == [3, None]


def test_recall_all_found_and_mrr() -> None:
    per_question = [[1], [3, None], [None], []]  # the last one has no gold: skipped
    assert recall_at(per_question, 1) == round((1 + 0 + 0) / 3, 4)
    assert recall_at(per_question, 5) == round((1 + 0.5 + 0) / 3, 4)
    assert all_found_at(per_question, 5) == round(1 / 3, 4)
    assert mrr(per_question) == round((1 + 1 / 3 + 0) / 3, 4)
    assert recall_at([], 5) is None


def test_approximate_vs_exact_overlap() -> None:
    assert overlap(["a", "b", "c"], ["a", "b", "d"]) == round(2 / 3, 4)
    assert overlap([], []) is None


def test_abstention_counts_refusals_and_misses() -> None:
    #           answered, refused (wrong), abstained (right), answered (missed)
    abstained = [False, True, True, False]
    unanswerable = [False, False, True, True]
    result = abstention(abstained, unanswerable)
    assert (result.precision, result.recall) == (0.5, 0.5)
    assert (result.false_abstentions, result.missed) == (1, 1)


def test_the_gate_sweep_abstains_beyond_each_threshold() -> None:
    rows = sweep([0.2, 0.45, 0.6, None], [False, False, True, True], [0.4, 0.5, 0.7])
    assert [(r.abstention.false_abstentions, r.abstention.missed) for r in rows] == [
        (1, 0),  # 0.4: refuses the 0.45 question, abstains on both unanswerable
        (0, 0),  # 0.5: perfect
        (0, 1),  # 0.7: answers the 0.6 question it should not
    ]
