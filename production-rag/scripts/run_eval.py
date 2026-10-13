"""
Evaluation CLI.

  python scripts/run_eval.py --mode retrieval [--corpus fixture|real]
  python scripts/run_eval.py --mode generation [--corpus fixture|real] [--no-entailment]

retrieval mode: ablates BM25-only / vector-only / hybrid / hybrid+rerank
against the answerable questions in eval_sets/gold.json and prints/saves a
comparison table (Hit Rate@k, Recall@k, MRR, nDCG@k).

generation mode: runs the full retrieve->rerank->generate pipeline on every
gold question and reports faithfulness/citation precision/recall, refusal
accuracy, and answer relevance. If eval_sets/baseline_metrics.json exists,
flags any metric that regressed beyond the configured threshold (this is
what scripts/ci_eval_gate.py wraps for CI).
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.eval.build_eval_index import get_retriever  # noqa: E402
from src.eval.generation_metrics import (  # noqa: E402
    aggregate_generation_metrics,
    answer_relevance,
    citation_precision,
    citation_recall,
    refused_correctly,
)
from src.eval.retrieval_metrics import aggregate_metrics  # noqa: E402
from src.generate.generator import RAGGenerator  # noqa: E402
from src.rerank.reranker import CrossEncoderReranker  # noqa: E402

GOLD_PATH = REPO_ROOT / "eval_sets" / "gold.json"
RUNS_DIR = REPO_ROOT / "eval_sets" / "runs"
RETRIEVAL_K = 5
RETRIEVE_CANDIDATES = 20
RERANK_TOP_N = 5


def load_gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))["items"]


def run_retrieval_ablation(corpus: str) -> dict:
    retriever = get_retriever(corpus)
    gold = [g for g in load_gold() if g["is_answerable"]]

    ranked_ids_by_mode: dict[str, list[list[str]]] = {"bm25": [], "vector": [], "hybrid": [], "hybrid+rerank": []}
    relevant_ids = [set(g["expected_chunk_ids"]) for g in gold]

    reranker = CrossEncoderReranker()

    for g in gold:
        for mode in ("bm25", "vector", "hybrid"):
            results = retriever.search(g["question"], mode=mode, limit=RETRIEVAL_K)
            ranked_ids_by_mode[mode].append([r.chunk_id for r in results])

        candidates = retriever.search(g["question"], mode="hybrid", limit=RETRIEVE_CANDIDATES)
        reranked = reranker.rerank(g["question"], candidates, top_n=RETRIEVAL_K)
        ranked_ids_by_mode["hybrid+rerank"].append([r.chunk_id for r in reranked])

    table = {
        mode: aggregate_metrics(ranked_ids, relevant_ids, k=RETRIEVAL_K)
        for mode, ranked_ids in ranked_ids_by_mode.items()
    }

    print(f"\nRetrieval ablation (k={RETRIEVAL_K}, n={len(gold)} answerable questions):")
    print(f"{'mode':<15}{'hit_rate@k':>12}{'recall@k':>12}{'mrr':>10}{'ndcg@k':>10}")
    for mode, m in table.items():
        print(f"{mode:<15}{m['hit_rate@k']:>12.3f}{m['recall@k']:>12.3f}{m['mrr']:>10.3f}{m['ndcg@k']:>10.3f}")

    return table


def run_generation_eval(corpus: str, run_entailment: bool, check_relevance: bool, model: str) -> dict:
    retriever = get_retriever(corpus)
    reranker = CrossEncoderReranker()
    generator = RAGGenerator(model=model, run_entailment_check=run_entailment)
    gold = load_gold()

    rows = []
    for g in gold:
        candidates = retriever.search(g["question"], mode="hybrid", limit=RETRIEVE_CANDIDATES)
        reranked = reranker.rerank(g["question"], candidates, top_n=RERANK_TOP_N)
        context = [{"chunk_id": r.chunk_id, "text": r.payload["text"]} for r in reranked]

        result = generator.answer(g["question"], context)

        row = {
            "id": g["id"],
            "question": g["question"],
            "status": result.status,
            "citation_precision": citation_precision(result),
            "citation_recall": citation_recall(result, set(g["expected_chunk_ids"])),
            "refused_correctly": refused_correctly(result, g["is_answerable"]),
        }
        if check_relevance and g["is_answerable"] and result.status in ("answered", "partially_supported"):
            row["answer_relevant"] = answer_relevance(g["question"], result.answer)
        rows.append(row)
        print(f"  {g['id']:<20} status={result.status:<20} "
              f"citation_precision={row['citation_precision']} refused_correctly={row['refused_correctly']}")

    agg = aggregate_generation_metrics(rows)
    print("\nGeneration eval aggregate:")
    for k, v in agg.items():
        print(f"  {k}: {v}")

    return {"model": model, "rows": rows, "aggregate": agg}


def check_regression(current: dict, baseline_path: Path, thresholds: dict[str, float]) -> list[str]:
    if not baseline_path.exists():
        return []
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["aggregate"]
    failures = []
    for metric, min_drop_allowed in thresholds.items():
        cur_val, base_val = current["aggregate"].get(metric), baseline.get(metric)
        if cur_val is None or base_val is None:
            continue
        if cur_val < base_val - min_drop_allowed:
            failures.append(f"{metric} regressed: {base_val:.3f} -> {cur_val:.3f} (allowed drop: {min_drop_allowed})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["retrieval", "generation"], required=True)
    parser.add_argument("--corpus", choices=["fixture", "real"], default="fixture")
    parser.add_argument("--no-entailment", action="store_true")
    parser.add_argument("--no-relevance", action="store_true")
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama model tag to use for generation")
    parser.add_argument("--save-baseline", action="store_true", help="Write result as eval_sets/baseline_metrics.json")
    args = parser.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    if args.mode == "retrieval":
        table = run_retrieval_ablation(args.corpus)
        out_path = RUNS_DIR / f"retrieval_ablation_{timestamp}.json"
        out_path.write_text(json.dumps(table, indent=2), encoding="utf-8")
        print(f"\nSaved to {out_path.relative_to(REPO_ROOT)}")
        return 0

    result = run_generation_eval(args.corpus, not args.no_entailment, not args.no_relevance, args.model)
    out_path = RUNS_DIR / f"generation_eval_{timestamp}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved to {out_path.relative_to(REPO_ROOT)}")

    baseline_path = REPO_ROOT / "eval_sets" / "baseline_metrics.json"
    if args.save_baseline:
        baseline_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Wrote new baseline to {baseline_path.relative_to(REPO_ROOT)}")
    else:
        thresholds = {"citation_precision": 0.10, "refusal_accuracy": 0.10}
        failures = check_regression(result, baseline_path, thresholds)
        if failures:
            print("\nREGRESSION DETECTED:")
            for f in failures:
                print(f"  - {f}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
