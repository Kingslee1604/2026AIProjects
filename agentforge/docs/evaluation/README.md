# Evaluation

How well AgentForge finds the right passage and answers only from it, measured on a gold dataset
(R1-40). Each dated folder holds one measurement: `report.md` for reading and `report.json`
with every number and every answer.

## Running it

```bash
make up      # the stack, with Ollama and the models on the host
make eval    # all 40 questions with the configured chat model
make eval EVAL_ARGS="--questions demo --chat-model ollama:qwen3:4b --chat-model ollama:qwen2.5:7b"
make eval EVAL_ARGS="--retrieval-only"   # minutes instead of tens of minutes
```

`make eval` builds the demo corpus, runs `agentforge eval run` inside the API container (same
code, settings, database, and models as the app), and copies the report to
`docs/evaluation/<date>/` (set `EVAL_OUT` to choose). The corpus is loaded into a temporary
knowledge base that is deleted afterwards; the answers' model calls are kept in memory, not in
the app's call log.

## The dataset

`demo/eval/gold.json`: the 40 questions of the Northwind questionnaire, written for this
project (CC-BY-4.0), against ten policy documents (Markdown, one DOCX, one PDF) and one
**adversarial** document, a "customer FAQ draft" that makes false claims (FedRAMP High, PCI)
and tries to instruct the model. For each question: whether the policies answer it, the
expected answer, and the exact quotes that prove it. 6 questions are unanswerable by design;
3 are injection targets. `make demo-check` verifies that every gold quote appears in the parsed
documents.

## What is measured

**Retrieval** (no model writing, only search):

- *Recall@k*: of the gold quotes, the share found in a passage from the right document among
  the top k results. *All quotes found@k*: the share of questions whose every quote is in the
  top k (multi-part answers).
- *MRR*: 1 / rank of the first passage holding a gold quote, averaged (1.0 = always first).
- *Approximate vs exact*: the overlap of the vector index's top 10 (HNSW with filtered
  iterative scan, as the app runs it) with an exact full scan. Below 100% the index loses
  results.

**Abstention gate**: before calling the model, the pipeline abstains when no retrieved
passage is within a cosine distance threshold (`AGENTFORGE_QA_MAX_DISTANCE`). The report sweeps
the threshold and counts both kinds of mistake: refusing an answerable question, and passing
an unanswerable one to the model.

**Answers** (the full pipeline with the real model: draft with quotes, mechanical checks,
verifier):

- *Abstention precision / recall*: "insufficient evidence" is the positive class; it should
  happen exactly on the unanswerable questions.
- *Answers citing a gold passage*: of the answered answerable questions, the share where a kept
  sentence cites a passage containing a gold quote.
- *Citation validity*: of the kept sentences, the share mechanically proven (a verbatim quote
  from the cited passage that says what the sentence says).
- *Unsupported-claim rate*: kept sentences not mechanically proven, plus every sentence kept
  for a question the documents do not answer, over all kept sentences.
- *Injection targets resisted*: no kept sentence cites the adversarial document, and an
  unanswerable target is answered "insufficient evidence".
- *Structured output validity*: the share of draft and verify calls whose reply parsed into the
  required JSON schema (and on the first try, before the one repair attempt).
- Time per question (p50, p95), model calls, and tokens.

Every report's manifest records the commit, the dataset hash, the models and their Ollama
digests, the settings, and the hardware. Numbers come from a CPU-only laptop and are only
comparable with reports from the same machine.

## Baseline and decisions (2026-10-16)

Reference machine: Dell Inspiron 15 3520, Intel Core i7-1255U, no GPU, Ollama on Windows,
the stack in WSL2 (4 GB). The commit is recorded as `f068765-dirty`: the harness of R1-40 on
top of that commit, before it was committed.

- [`2026-10-16-baseline`](2026-10-16-baseline/report.md): all 40 questions, `qwen3:4b`.
- [`2026-10-16-models`](2026-10-16-models/report.md): the 12 demo questions with the three
  installed chat models.

**Retrieval** is not the bottleneck: recall@5 100% (every gold quote of every answerable
question is in the top 5), MRR 0.95, and the HNSW index returns the same top 10 as an exact
scan. The embedding model stays `nomic-embed-text:v1.5`; with retrieval saturated on this
corpus there is nothing for another embedding model to win here (only this one is installed;
a larger corpus may change that).

**Chat model: `qwen3:4b` stays the default.**

| Model (12 demo questions) | Refused answerable (of 10) | Answered unanswerable (of 2) | Injection targets resisted | p50 s / question |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | **0** | 1 | 1 of 2 | **32.7** |
| `qwen2.5:7b` | 3 | **0** | **2 of 2** | 53.7 |
| `llama3.2` (3B) | 5 | 2 | 0 of 2 | 33.5 |

In a workflow where a person reviews every answer, refusing an answerable question costs that
person a manual answer, while a wrong answer is caught in review only if the reviewer notices.
`qwen3:4b` answers every answerable question from the right passage and is the fastest;
`qwen2.5:7b` resists the injected FAQ but refuses 3 of 10 answerable questions and is 64%
slower; `llama3.2` is worse on every axis. All three produced valid structured output on every
call.

**Abstention threshold: unchanged at 0.5.** On this corpus every unanswerable question has a
passage within 0.45, so at 0.5 the gate never fires and abstention is left to the drafter. A threshold of 0.35 would have stopped three of the four wrong answers in the
baseline, but the hardest answerable question sits at 0.341: a 0.009 margin that a reworded
question would cross. That is tuning to the test set, not a fix.

**What the baseline found** (all 40 questions, `qwen3:4b`): 34 of 34 answerable questions
answered from a gold passage, 0 refused; 2 of 6 unanswerable questions abstained. The other
four are the open problems:

1. *Answers that are true but off-topic* (Q18, Q19): the passages say something near the
   question (a DPA instead of a HIPAA BAA; log reviews instead of the security team's size)
   and the sentence is quoted correctly, so every mechanical check passes. Nothing checks that
   the answer answers the question.
2. *Trusting an adversarial document* (Q38, Q39): the draft FAQ claims FedRAMP High and PCI
   DSS Level 1; the claims are quoted verbatim from it, so they pass verification. The
   injected *instructions* in that document were not followed; its false *statements* were
   believed. Every document in a knowledge base is treated as equally authoritative.
3. Minor: some answers repeat a sentence (Q14, Q32), when two claims quote the same passage.

## Relevance check (2026-10-16)

Problems 1 and 3 are fixed: after verification, one model call asks whether the answer responds
to the question (`relevance` agent: answers / partially / does not answer; "does not answer"
becomes insufficient evidence, "partially" lowers confidence), and a claim that repeats an
earlier sentence is left out without lowering confidence. Setting: `AGENTFORGE_QA_RELEVANCE_CHECK`
(on by default). Measured in [`2026-10-16-relevance`](2026-10-16-relevance/report.md), all 40
questions with `qwen3:4b`:

| | Baseline | With the relevance check |
| --- | --- | --- |
| Answerable refused (of 34) | 0 | **0** |
| Unanswerable answered (of 6) | 4 | **2** (Q38, Q39: the adversarial FAQ) |
| Unsupported-claim rate | 14.8% | **8.5%** |
| Repeated sentences (Q14, Q32) | yes | no |
| Model calls for 40 questions | 41 | 83 |
| Seconds per question p50 / p95 | 43.9 / 73.7 | 72.1 / 108.8 |

The time is not a like-for-like comparison: during the second run the laptop was on battery
(Windows throttles the CPU) with 0.2 GB of 16 GB memory free. The relevance call itself is one
short call per answered question; R1-49 measures time on mains power. Problem 2 (statements of
an adversarial document believed) remains open: "Northwind holds FedRAMP High authorization"
does answer the FedRAMP question; it is false, not off-topic.

