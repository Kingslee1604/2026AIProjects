# Ask My Cardiology Docs

A domain-specific ("cardiology") RAG system with hybrid retrieval, cross-encoder reranking,
enforced+verified citations, and a CI-gated evaluation pipeline — built to run **entirely
offline on a CPU-only laptop**. Heavy one-off steps (PDF/table/figure parsing) run as a batch
job on free Colab GPU; nothing in the live app calls a paid API or requires a GPU.

Full design rationale is in [`sources.json`](sources.json) (data provenance) and the plan this
was built from; this README covers architecture, how to run it, and real measured results.

## Architecture

```
PDFs (WHO guidelines, FDA drug labels, PMC cardiology articles)
        │  scripts/download_corpus.py  (documented public APIs, no guessed URLs)
        ▼
data/raw/  +  data/raw/manifest.json  (provenance: url, license, sha256, retrieved_at)
        │  notebooks/01_docling_parse_corpus.ipynb  (Colab GPU, one-off batch)
        │  Docling → typed elements (text / table / figure) + BLIP figure captions
        ▼
data/processed/*.json
        │  scripts/build_chunks.py  →  src/chunk/chunker.py
        │  structure-aware chunking: tables & figures are atomic, never split mid-element
        ▼
data/chunks/chunks.jsonl
        │  scripts/build_index.py  →  src/index/hybrid_index.py
        │  Qdrant (local/embedded) — dense (BAAI/bge-small-en-v1.5) + sparse BM25, one store
        ▼
data/qdrant_local/
        │  src/retrieve/retriever.py  — bm25 | vector | hybrid (RRF fusion), all CPU
        ▼
        │  src/rerank/reranker.py  — cross-encoder/ms-marco-MiniLM-L-6-v2, CPU
        ▼
        │  src/generate/generator.py  — local LLM via Ollama, structured citations
        │  src/generate/citation_checker.py — grounding check + entailment check
        ▼
Answer + verified citations, or a safe refusal
```

Live query path: retrieve → rerank → generate → citation-verify. Everything left of
`data/processed/*.json` runs once, offline, ahead of time.

## Citation enforcement

Every answer is structured JSON (`answer`, `citations[]`, `insufficient_evidence`), enforced via
Ollama's schema-constrained output. Two independent checks run before an answer is shown:

1. **Grounding check** (`src/generate/citation_checker.py::grounding_check`) — cheap, no LLM
   call: rejects any citation pointing at a `chunk_id` that was never actually retrieved
   (catches hallucinated sources).
2. **Entailment check** — one LLM-judge call per citation, confirming the cited chunk's text
   actually supports the specific claim (not just the same general topic).

A third, code-level check (`generator.py`) rejects answers with **zero citations** outright —
found during development (see Engineering notes below) that the small local model will
sometimes produce a confident, uncited answer without being asked to.

Result statuses: `answered`, `partially_supported`, `insufficient_evidence` (model itself
declined), `uncited_answer` / `citation_violation` (blocked by the enforcement layer, shown as a
safe fallback rather than an unverified answer).

## Data

~71 real documents, ~199 files total, all downloaded via **documented public APIs** (NCBI
E-utilities + PMC Cloud Service on AWS, DailyMed REST API, direct WHO IRIS URLs verified by hand)
— see `sources.json` and `data/raw/manifest.json` for exact resolved URLs, licenses, and sha256
hashes of every file:

| Source | Count | License |
|---|---|---|
| WHO clinical guidelines (hypertension, CVD risk, rheumatic heart disease) | 3 | CC BY-NC-SA 3.0 IGO |
| FDA drug labels via DailyMed (statins, anticoagulants, beta-blockers, etc.) | 8 | Public domain |
| PMC Open Access cardiology case reports / reviews (+ 128 embedded figure images) | 60 | CC BY |

## Evaluation results

Two eval sets exist: a **fixture set** (12 hand-verified questions over a small synthetic
corpus, `eval_sets/gold.json`) used to prove the harness itself is correct, and the path to a
real ~120-150 question set once the Colab notebook has been run once (see `scripts/run_eval.py
--corpus real`).

### Retrieval ablation (fixture corpus, k=5, n=9 answerable questions)

| Mode | Hit Rate@5 | Recall@5 | MRR | nDCG@5 |
|---|---|---|---|---|
| BM25 only | 1.000 | 1.000 | 0.889 | 0.925 |
| Vector only | 1.000 | 1.000 | 0.944 | 0.950 |
| Hybrid (RRF) | 1.000 | 1.000 | 0.944 | 0.950 |
| Hybrid + rerank | 0.889 | 0.889 | 0.889 | 0.889 |

### Generation quality (fixture corpus, n=12): model comparison

| Metric | llama3.2:3b | qwen2.5:7b (default) |
|---|---|---|
| Citation precision | 0.188 | **0.875** |
| Citation recall | 0.222 | **0.667** |
| Refusal accuracy | 0.417 | **0.833** |

Both models ran through the identical pipeline (same prompt, same citation-enforcement checks,
same 12 gold questions) — the only variable is model size (3B vs 7B). qwen2.5:7b is the
committed default (`src/generate/generator.py::DEFAULT_MODEL`) and its run is what's committed
as `eval_sets/baseline_metrics.json` — the CI eval gate fails a PR that regresses citation
precision or refusal accuracy by more than 0.10 against this baseline.

## Engineering notes (real findings, not aspirational)

- **Local model size is the actual bottleneck, not the retrieval pipeline — and it's a large,
  measured effect, not a marginal one.** Retrieval hit rate is ~100% at this corpus scale
  regardless of model. Generation citation precision went 0% (first prompt version, model
  answered confidently while leaving `citations: []`) → 19% (llama3.2:3b, after adding one
  worked example to the system prompt) → **88% (qwen2.5:7b, same prompt, same corpus, same
  questions)**. Both models ran on the same CPU-only laptop; qwen2.5:7b is noticeably slower
  per answer but still usable interactively. This is the quality-vs-size tradeoff Project 2 is
  about, measured end-to-end through an actual citation-enforcement pipeline rather than a
  generic benchmark. Remaining gap even at 7B: the one multi-hop question (needs facts from two
  different chunks combined) still falls back to `insufficient_evidence` — worth checking
  whether a larger rerank window or an explicit multi-hop prompt hint fixes it.
- **Reranking made retrieval look slightly worse at n=9.** This is very likely a small-sample
  artifact (one multi-hop question needing two specific chunks in a 5-slot rerank window) rather
  than evidence reranking doesn't help — needs re-checking on the real ~1,600+-chunk corpus
  before drawing a conclusion either way. Flagged here rather than hidden.
- **ragas was evaluated and dropped.** The published release has a broken import
  (`ragas.llms.base` imports `langchain_community.chat_models.vertexai`, which doesn't exist in
  current `langchain_community`), confirmed by installing both. `src/eval/generation_metrics.py`
  implements the same metrics directly against the already-tested Ollama-judge calls in
  `src/generate/`.
- **The PMC "Open Access Web Service" (`oa.fcgi` → FTP) is being retired in August 2026.**
  `scripts/download_corpus.py` uses the newer PMC Cloud Service (public S3 bucket,
  `pmc-oa-opendata.s3.amazonaws.com`) instead, which also turned out to expose each article's
  embedded figure images directly — used for the figure/image-grounded eval questions instead of
  extracting images from the PDF.

## Running it

This project lives in the `production-rag/` folder of the
[2026AIProjects](https://github.com/Kingslee1604/2026AIProjects) repository; run every command
below from that folder. Its CI workflow is at
[`.github/workflows/production-rag-eval-gate.yml`](../.github/workflows/production-rag-eval-gate.yml).

```bash
cd production-rag
pip install -r requirements.txt

# 1. Download the corpus (real PDFs, ~71 documents, documented public APIs)
python scripts/download_corpus.py

# 2. Parse it: upload data/raw/ to notebooks/01_docling_parse_corpus.ipynb on Colab (GPU),
#    run all cells, unzip the result into data/processed/

# 3. Build chunks + hybrid index (local, CPU)
python scripts/build_chunks.py
python scripts/build_index.py

# 4. Pull the default local LLM (see "Generation quality" above for why 7B, not 3B)
ollama pull qwen2.5:7b

# 5. Run the demo app
streamlit run app/main.py

# 6. Run evals
python scripts/run_eval.py --mode retrieval --corpus real
python scripts/run_eval.py --mode generation --corpus real
```

## Tests

```bash
python -m pytest tests/ -v
```

47 tests, all passing against real components (real Qdrant hybrid index, real cross-encoder
reranker, real Ollama-served qwen2.5:7b) — no mocks in the core pipeline: chunker (table/figure
atomicity), hybrid retrieval, reranking, citation grounding/entailment, retrieval + generation
metrics, and a full pipeline integration test. Ollama-dependent tests self-skip when no Ollama
daemon is reachable (e.g. in hosted CI).
