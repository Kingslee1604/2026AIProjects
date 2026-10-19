# Models

AgentForge runs open-weight models locally through [Ollama](https://ollama.com). No paid API is
needed, and no document text leaves the machine unless you configure a remote endpoint.

## Defaults

| Role | Model | Why |
|---|---|---|
| Chat (drafting, verifying, relevance, planning) | `qwen3:4b` | Best of the three tested on CPU: 0 answerable questions refused, fastest. See below |
| Embeddings | `nomic-embed-text:v1.5` | Retrieval recall@5 100% and MRR 0.94 on the demo corpus |

```bash
ollama pull qwen3:4b
ollama pull nomic-embed-text:v1.5
```

## Measured comparison (CPU-only laptop, 12 demo questions)

From [docs/evaluation](../evaluation/README.md) (2026-10-16, Intel i7-1255U, no GPU):

| Model | Answerable refused (of 10) | Unanswerable answered (of 2) | Injection targets resisted | Seconds per question (p50) |
|---|---|---|---|---|
| `qwen3:4b` (default) | **0** | 1 | 1 of 2 | **32.7** |
| `qwen2.5:7b` | 3 | **0** | **2 of 2** | 53.7 |
| `llama3.2` (3B) | 5 | 2 | 0 of 2 | 33.5 |

All three produced valid structured output on every call. Numbers from other machines are not
comparable: measure yours with `make eval` (see the evaluation README).

## Changing models

In a `compose.override.yaml` next to `compose.yaml` (git-ignored; see
[configuration](configuration.md)), then `make up`:

```yaml
x-models: &models
  AGENTFORGE_CHAT_MODEL: ollama:qwen2.5:7b
  AGENTFORGE_CHAT_FALLBACK_MODELS: '["ollama:qwen3:4b"]'   # tried if the first fails
services:
  api:
    environment: *models
  worker:
    environment: *models
```

- **More RAM or a GPU:** an 8B model (for example `qwen3:8b`) is more careful but slower. On
  16 GB of RAM without a GPU it pushes the machine into paging.
- **OpenAI-compatible server** (vLLM, LM Studio, or a hosted API):
  `AGENTFORGE_CHAT_MODEL=openai_compatible:<model>` with `AGENTFORGE_OPENAI_BASE_URL` and, if
  needed, `AGENTFORGE_OPENAI_API_KEY`. A remote endpoint receives your document text.
- **Embedding model:** changing it changes the vectors, so re-index every document (the re-index
  button in each document's row on the knowledge base page).
- **No model at all:** `AGENTFORGE_MODEL_PROVIDER=fake` gives deterministic answers that quote
  the top passage. This is for trying the interface and for CI.

## Licenses

Check each model's license on its model card before you use it in a product. Both defaults
are published under Apache-2.0 (Qwen3, nomic-embed-text v1.5). Models with custom use restrictions (for example
Llama and Gemma) can be selected but are not defaults (ADR-0007).
