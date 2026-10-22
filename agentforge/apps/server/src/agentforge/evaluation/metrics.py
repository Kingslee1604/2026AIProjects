"""Metric definitions (pure functions, unit-tested). Every rate is None when undefined (0/0)."""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

from agentforge.evaluation.dataset import GoldEvidence, is_relevant


@dataclass(frozen=True, slots=True)
class Passage:
    filename: str
    content: str
    vector_distance: float | None = None


def ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def gold_ranks(passages: Sequence[Passage], evidence: Sequence[GoldEvidence]) -> list[int | None]:
    """For each gold quote, the 1-based rank of the first passage that contains it (in the
    right document), or None when no retrieved passage does."""
    ranks: list[int | None] = []
    for gold in evidence:
        rank = next(
            (
                i
                for i, p in enumerate(passages, start=1)
                if is_relevant(p.filename, p.content, gold)
            ),
            None,
        )
        ranks.append(rank)
    return ranks


def recall_at(per_question: Sequence[Sequence[int | None]], k: int) -> float | None:
    """Mean over questions of the share of gold quotes found in the top k (questions with no
    gold quotes are skipped)."""
    scores = [
        sum(1 for r in ranks if r is not None and r <= k) / len(ranks)
        for ranks in per_question
        if ranks
    ]
    return ratio(sum(scores), len(scores))


def all_found_at(per_question: Sequence[Sequence[int | None]], k: int) -> float | None:
    """Share of questions whose every gold quote is in the top k (multi-part answers)."""
    scored = [ranks for ranks in per_question if ranks]
    found = sum(1 for ranks in scored if all(r is not None and r <= k for r in ranks))
    return ratio(found, len(scored))


def mrr(per_question: Sequence[Sequence[int | None]]) -> float | None:
    """Mean reciprocal rank of the first passage holding any gold quote (0 when none does)."""
    scored = [ranks for ranks in per_question if ranks]
    total = 0.0
    for ranks in scored:
        found = [r for r in ranks if r is not None]
        total += 1 / min(found) if found else 0.0
    return ratio(total, len(scored))


def overlap(approximate: Sequence[Hashable], exact: Sequence[Hashable]) -> float | None:
    """Share of the exact top-k that the approximate (index) search also returned."""
    return ratio(len(set(approximate) & set(exact)), len(exact))


@dataclass(frozen=True, slots=True)
class Abstention:
    """Abstaining is the positive class: it should happen exactly on unanswerable questions."""

    precision: float | None  # of the questions it abstained on, the share that were unanswerable
    recall: float | None  # of the unanswerable questions, the share it abstained on
    false_abstentions: int  # answerable questions it refused
    missed: int  # unanswerable questions it answered anyway


def abstention(abstained: Sequence[bool], unanswerable: Sequence[bool]) -> Abstention:
    pairs = list(zip(abstained, unanswerable, strict=True))
    true_positive = sum(1 for a, u in pairs if a and u)
    return Abstention(
        precision=ratio(true_positive, sum(1 for a, _ in pairs if a)),
        recall=ratio(true_positive, sum(1 for _, u in pairs if u)),
        false_abstentions=sum(1 for a, u in pairs if a and not u),
        missed=sum(1 for a, u in pairs if u and not a),
    )


@dataclass(frozen=True, slots=True)
class GateRow:
    threshold: float
    abstention: Abstention


def sweep(
    best_distances: Sequence[float | None],
    unanswerable: Sequence[bool],
    thresholds: Sequence[float],
) -> list[GateRow]:
    """The retrieval gate alone: abstain when no passage is within the threshold (cosine
    distance), as the answer pipeline does before calling the model."""
    return [
        GateRow(
            threshold=t,
            abstention=abstention([d is None or d > t for d in best_distances], unanswerable),
        )
        for t in thresholds
    ]
