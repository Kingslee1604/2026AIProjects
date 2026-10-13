import math

import pytest

from src.eval.retrieval_metrics import (
    aggregate_metrics,
    hit_rate_at_k,
    ndcg_at_k,
    reciprocal_rank,
    recall_at_k,
)


def test_hit_rate_at_k_hit_within_k():
    assert hit_rate_at_k(["a", "b", "c"], {"c"}, k=3) == 1.0


def test_hit_rate_at_k_miss_outside_k():
    assert hit_rate_at_k(["a", "b", "c", "d"], {"d"}, k=2) == 0.0


def test_recall_at_k_partial():
    # 1 of 2 relevant docs found in top 3
    assert recall_at_k(["a", "x", "b"], {"a", "b", "c"}, k=1) == 1 / 3
    assert recall_at_k(["a", "x", "b"], {"a", "b", "c"}, k=3) == 2 / 3


def test_recall_at_k_empty_relevant_set_is_zero_not_divide_by_zero():
    assert recall_at_k(["a", "b"], set(), k=2) == 0.0


def test_reciprocal_rank_first_position():
    assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0


def test_reciprocal_rank_third_position():
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == 1 / 3


def test_reciprocal_rank_no_hit_is_zero():
    assert reciprocal_rank(["x", "y", "z"], {"a"}) == 0.0


def test_ndcg_at_k_perfect_ranking_is_one():
    # single relevant doc placed first -> DCG == IDCG -> nDCG == 1.0
    assert ndcg_at_k(["a", "x", "y"], {"a"}, k=3) == pytest.approx(1.0)


def test_ndcg_at_k_relevant_doc_lower_in_ranking_scores_less_than_one():
    score_first = ndcg_at_k(["a", "x"], {"a"}, k=2)
    score_second = ndcg_at_k(["x", "a"], {"a"}, k=2)
    assert score_second < score_first
    assert score_second == pytest.approx(1.0 / math.log2(3))


def test_ndcg_at_k_no_relevant_docs_in_window_is_zero():
    assert ndcg_at_k(["x", "y"], {"a"}, k=2) == 0.0


def test_aggregate_metrics_averages_across_queries():
    ranked = [["a", "b"], ["x", "y"]]
    relevant = [{"a"}, {"z"}]  # query 1 hits, query 2 misses entirely
    agg = aggregate_metrics(ranked, relevant, k=2)
    assert agg["n"] == 2
    assert agg["hit_rate@k"] == 0.5
    assert agg["mrr"] == 0.5  # (1.0 + 0.0) / 2


def test_aggregate_metrics_on_empty_input():
    agg = aggregate_metrics([], [], k=5)
    assert agg["n"] == 0
    assert agg["hit_rate@k"] == 0.0
