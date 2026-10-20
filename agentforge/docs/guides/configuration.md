# Configuration

Settings are environment variables; every application setting starts with `AGENTFORGE_`. The
full list, with types and limits, is `apps/server/src/agentforge/core/settings.py`.

**Where to put them with Docker Compose.** `.env` (created by `make up`, git-ignored, readable
only by you) holds the passwords and ports, plus four settings that Compose passes to the
containers: `AGENTFORGE_LOG_LEVEL`, `AGENTFORGE_MODEL_PROVIDER`,
`AGENTFORGE_OLLAMA_BASE_URL`, and `AGENTFORGE_RUN_WORKER_CONCURRENCY`. **Any other setting goes in a `compose.override.yaml`** next to
`compose.yaml`. Compose loads that file automatically, and it is git-ignored. Set it on both
services, then run `make up`:

```yaml
# compose.override.yaml
x-settings: &settings
  AGENTFORGE_CHAT_MODEL: ollama:qwen2.5:7b
  AGENTFORGE_RATE_ANSWERS_PER_MINUTE: "40"
services:
  api:
    environment: *settings
  worker:
    environment: *settings
```

Outside Compose (`uv run agentforge api`), plain environment variables work.

## Stack (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD`, `AGENTFORGE_APP_DB_PASSWORD`, `AGENTFORGE_ENGINE_DB_PASSWORD` | random, generated on first `make up` | Passwords of the owner, app, and engine database roles. Never commit them |
| `POSTGRES_PORT` / `API_PORT` / `WEB_PORT` | 5432 / 8000 / 3000 | Ports published on 127.0.0.1 |
| `AGENTFORGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs never contain secrets, even at `DEBUG` (tested) |

## Models

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_MODEL_PROVIDER` | `auto` | `auto` uses the configured models; `fake` uses deterministic offline models (no Ollama; CI uses it) |
| `AGENTFORGE_OLLAMA_BASE_URL` | `http://host.docker.internal:11434` in Compose | Where Ollama is |
| `AGENTFORGE_CHAT_MODEL` | `ollama:qwen3:4b` | `provider:model`; see [models](models.md) |
| `AGENTFORGE_CHAT_FALLBACK_MODELS` | `[]` | JSON list tried in order when the chat model fails, e.g. `["ollama:qwen2.5:7b"]` |
| `AGENTFORGE_EMBEDDING_MODEL` | `ollama:nomic-embed-text:v1.5` | Changing it requires re-indexing every document |
| `AGENTFORGE_OPENAI_BASE_URL`, `AGENTFORGE_OPENAI_API_KEY` | unset | An OpenAI-compatible endpoint for `openai_compatible:` models. Document text is sent to it |
| `AGENTFORGE_MODEL_TIMEOUT_SECONDS` | 180 | Per call |

## Answers

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_QA_TOP_K` | 6 | Passages given to the drafter (1–20) |
| `AGENTFORGE_QA_MAX_DISTANCE` | 0.5 | Abstain without a model call when no passage is this close (cosine distance) |
| `AGENTFORGE_QA_VERIFIER_MODE` | `low_confidence_only` | `all` verifies every claim with the model; `off` never does |
| `AGENTFORGE_QA_RELEVANCE_CHECK` | `true` | One more call per answer: does it answer the question? |
| `AGENTFORGE_QA_MAX_LLM_CALLS` | 12 | Budget per answer |

## Questionnaire runs

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_RUN_ITEM_PARALLELISM` | 4 | Questions answered at once per run |
| `AGENTFORGE_RUN_WORKER_CONCURRENCY` | **1 in Compose** (8 outside it) | Questions answered at once per worker, across all runs. A CPU-only Ollama answers one request at a time, so more only makes calls queue and time out: on the reference laptop the 12-question run took 31.2 min at 8 (14 of 42 calls timed out), **14.9 min at 1**, and 13.8 min at 2 (no timeouts, but the slowest calls reached 155 of the 180 s limit). Raise it in `.env` with a GPU |
| `AGENTFORGE_RUN_MAX_LLM_CALLS` / `AGENTFORGE_RUN_MAX_SECONDS` | 3000 / 21600 | Budget per run; a run that exceeds it fails visibly |

## Uploads and limits

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_MAX_UPLOAD_MB` | 25 | Per uploaded file |
| `AGENTFORGE_MAX_REQUEST_KB` | 1024 | Any other request body |
| `AGENTFORGE_RATE_UPLOADS_PER_MINUTE` | 30 | Per person or API key: documents and questionnaires |
| `AGENTFORGE_RATE_ANSWERS_PER_MINUTE` | 20 | Questions asked |
| `AGENTFORGE_RATE_RUNS_PER_MINUTE` | 10 | Questionnaire runs started |

## Sign-in and sessions

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_COOKIE_SECURE` | `false` | Must be `true` behind TLS; production refuses to start without it |
| `AGENTFORGE_SESSION_IDLE_MINUTES` / `AGENTFORGE_SESSION_ABSOLUTE_HOURS` | 120 / 12 | Session lifetime |
| `AGENTFORGE_LOGIN_WINDOW_MINUTES` | 15 | Window for counting failed sign-ins |
| `AGENTFORGE_LOGIN_MAX_FAILURES_PER_EMAIL` / `_PER_IP` | 5 / 20 | Lockout and throttle thresholds |

## MCP server

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_MCP_ALLOWED_ORIGINS` | `[]` | JSON list of browser origins allowed to call `/mcp` (programs send none) |
| `AGENTFORGE_MCP_REQUESTS_PER_MINUTE` / `AGENTFORGE_MCP_ANSWERS_PER_MINUTE` | 60 / 6 | Per API key |

## Deployment

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_ENV` | `development` | `production` requires `AGENTFORGE_COOKIE_SECURE=true` |
| `AGENTFORGE_API_HOST` / `AGENTFORGE_API_PORT` | 127.0.0.1 / 8000 | Outside a container the API refuses a non-loopback address unless `production` with a database configured |

The rest (database URLs, blob directory, worker heartbeat, engine executor ID, fake-model
latency and fault injection) is set by Compose or used by tests; see `settings.py`.
