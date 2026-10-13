"""
Streamlit demo app: cardiology "Ask My Docs" with hybrid retrieval, cross-
encoder reranking, and citation enforcement -- plus an eval dashboard so the
CI-gated evaluation pipeline is itself a visible part of the demo, not a
hidden test suite.

Run: streamlit run app/main.py
"""

import glob
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.eval.build_eval_index import get_retriever  # noqa: E402
from src.generate.generator import RAGGenerator  # noqa: E402
from src.rerank.reranker import CrossEncoderReranker  # noqa: E402

st.set_page_config(page_title="Ask My Cardiology Docs", layout="wide")

RETRIEVE_CANDIDATES = 20
STATUS_LABELS = {
    "answered": ("✅ Answered", "green"),
    "partially_supported": ("⚠️ Partially supported", "orange"),
    "insufficient_evidence": ("🚫 Insufficient evidence", "gray"),
    "uncited_answer": ("🚫 Blocked: no citations given", "red"),
    "citation_violation": ("🚫 Blocked: citation failed verification", "red"),
}


@st.cache_resource
def load_pipeline(corpus: str):
    return {
        "retriever": get_retriever(corpus),
        "reranker": CrossEncoderReranker(),
        "generator": RAGGenerator(run_entailment_check=True),
    }


def ask_tab():
    st.sidebar.header("Settings")
    corpus = st.sidebar.selectbox("Corpus", ["fixture", "real"], help=(
        "'fixture' is the small hand-built demo corpus. 'real' requires "
        "running scripts/build_chunks.py + scripts/build_index.py first "
        "(after the Colab parsing notebook)."
    ))
    retrieval_mode = st.sidebar.selectbox("Retrieval mode", ["hybrid", "vector", "bm25"], index=0)
    top_n = st.sidebar.slider("Chunks passed to the LLM (after rerank)", 2, 10, 5)

    st.title("Ask My Cardiology Docs")
    st.caption(
        "Hybrid (BM25 + vector) retrieval → cross-encoder rerank → local LLM generation "
        "with enforced, verified citations. Fully offline."
    )

    question = st.text_input("Ask a question about the indexed cardiology documents:")
    if not st.button("Ask") or not question:
        return

    try:
        pipeline = load_pipeline(corpus)
    except FileNotFoundError as e:
        st.error(str(e))
        return

    with st.spinner("Retrieving..."):
        candidates = pipeline["retriever"].search(question, mode=retrieval_mode, limit=RETRIEVE_CANDIDATES)

    with st.spinner("Reranking..."):
        reranked = pipeline["reranker"].rerank(question, candidates, top_n=top_n)
        context = [{"chunk_id": r.chunk_id, "text": r.payload["text"]} for r in reranked]

    with st.spinner("Generating answer..."):
        result = pipeline["generator"].answer(question, context)

    label, color = STATUS_LABELS.get(result.status, (result.status, "gray"))
    st.markdown(f":{color}[**{label}**]")
    st.write(result.answer)

    if result.citations:
        st.subheader("Citations")
        chunk_text_by_id = {c["chunk_id"]: c["text"] for c in context}
        entailment_by_chunk = {e.chunk_id: e for e in result.entailment_results}
        for c in result.citations:
            verdict = entailment_by_chunk.get(c["chunk_id"])
            verdict_str = ""
            if verdict is not None:
                verdict_str = " ✅ verified" if verdict.supported else " ❌ NOT verified by entailment check"
            with st.expander(f"[{c['chunk_id']}]{verdict_str} — {c['claim'][:80]}"):
                st.write(f"**Claim:** {c['claim']}")
                st.write(f"**Quoted:** {c['quote']}")
                if verdict is not None:
                    st.write(f"**Entailment reasoning:** {verdict.reasoning}")
                st.write("**Full source chunk:**")
                st.code(chunk_text_by_id.get(c["chunk_id"], "(not found)"))

    if result.grounding_violations:
        st.error(f"Hallucinated citations blocked: {result.grounding_violations}")

    with st.expander(f"Retrieved & reranked candidates ({len(reranked)})"):
        rows = [{
            "chunk_id": r.chunk_id,
            "chunk_type": r.payload.get("chunk_type"),
            "retrieval_score": round(r.retrieval_score, 4),
            "rerank_score": round(r.rerank_score, 4),
            "text_preview": r.payload["text"][:120].replace("\n", " "),
        } for r in reranked]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)


def eval_dashboard_tab():
    st.title("Evaluation Dashboard")
    st.caption("The CI-gated eval pipeline, visualized -- not just a hidden test suite.")

    st.subheader("Retrieval ablation (BM25 vs vector vs hybrid vs hybrid+rerank)")
    retrieval_runs = sorted(glob.glob(str(REPO_ROOT / "eval_sets" / "runs" / "retrieval_ablation_*.json")))
    if not retrieval_runs:
        st.info("No retrieval ablation run found yet. Run: `python scripts/run_eval.py --mode retrieval`")
    else:
        table = json.loads(Path(retrieval_runs[-1]).read_text(encoding="utf-8"))
        df = pd.DataFrame(table).T
        st.dataframe(df, use_container_width=True)
        st.bar_chart(df[["recall@k", "ndcg@k"]])

    st.subheader("Generation quality (latest run vs. committed baseline)")
    baseline_path = REPO_ROOT / "eval_sets" / "baseline_metrics.json"
    generation_runs = sorted(glob.glob(str(REPO_ROOT / "eval_sets" / "runs" / "generation_eval_*.json")))

    cols = st.columns(2)
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["aggregate"]
        with cols[0]:
            st.write("**Baseline**")
            st.json(baseline)
    if generation_runs:
        latest = json.loads(Path(generation_runs[-1]).read_text(encoding="utf-8"))["aggregate"]
        with cols[1]:
            st.write("**Latest run**")
            st.json(latest)
    if not baseline_path.exists() and not generation_runs:
        st.info("No generation eval run found yet. Run: `python scripts/run_eval.py --mode generation`")


tab1, tab2 = st.tabs(["Ask", "Eval Dashboard"])
with tab1:
    ask_tab()
with tab2:
    eval_dashboard_tab()
