# Dependency license and version verification — 2026-10-13

Release 1 task **R1-02**. Closes the `[VERIFY]` items in architecture §20.2 that affect Release 1.

## Method

- **Sources (primary only):** PyPI JSON API (`License-Expression`, `License`, classifiers), npm
  registry, GitHub repository license files, Hugging Face model API, Ollama registry manifests.
  Retrieved 2026-10-13 (UTC).
- **Version rule:** the version listed is the newest stable release published on or before
  **2026-09-28**, matching the 14-day minimum-age policy (`exclude-newer` in `pyproject.toml`,
  `minimumReleaseAge` in `pnpm-workspace.yaml`). Exact versions are pinned in `uv.lock` and
  `pnpm-lock.yaml` by the task that first adds each dependency; a newer eligible version may be
  used at that time.
- **Enforcement:** `make licenses` (part of `make check`) evaluates every installed Python and npm
  package against [`licenses/policy.toml`](../../licenses/policy.toml) and fails the build on a
  violation. Proven by 27 unit tests and a live test that installed `html2text`
  (GPL-3.0-or-later): the gate failed and named the package.

**Status legend:** ✅ allowed by policy · ⚠️ allowed with a condition · ❗ needs a decision.

## Findings that need attention

| # | Finding | Impact | Action |
|---|---|---|---|
| F1 | **`psycopg` 3 (and `psycopg-binary`, `psycopg-pool`) is `LGPL-3.0-only`.** It is the Postgres driver the design prefers (architecture §0), and it is a required dependency of **DBOS** and **LangGraph's Postgres checkpointer**, two of the three engine candidates | LGPL is not in the allowlist, so the gate would block it | ❗ Decide via [ADR-0013](../adr/0013-lgpl-postgres-driver.md) (proposed: allow unmodified psycopg as a named exception) |
| F2 | `elkjs` (EPL-2.0), pulled in by `mermaid` for docs validation | Development only | ✅ Allowed under `dev_only`; the gate fails if it ever becomes a shipped dependency |
| F3 | `khroma` has no license field in `package.json` | Development only | ✅ Verified MIT from its repository and shipped license file; recorded as an exception |
| F4 | `pypdfium2` wheels bundle PDFium and its libraries (FreeType, libjpeg-turbo, OpenJPEG, LCMS, ICU, …) under permissive licenses | Their notices must ship with AgentForge images | ⚠️ Include `pypdfium2`'s bundled license files in `THIRD_PARTY_LICENSES` (task R1-50) |
| F5 | MCP project is relicensing from MIT to Apache-2.0 (MCP Inspector LICENSE); the Python SDK is still MIT | Both permissive | ✅ No action |
| F6 | `qdrant-client` 1.19 removed APIs (seen in production-rag) | Reminder that unpinned ranges break builds | ✅ Lockfiles + minimum-age policy already address this |

## Python packages

Scope: **S** = shipped with AgentForge, **D** = development only. "Task" is the Release 1 task that
first adds the package.

| Package | Version (≤ cutoff) | License (as published) | Scope | Task | Status |
|---|---|---|---|---|---|
| fastapi | 0.141.1 | MIT | S | R1-04 | ✅ |
| starlette | 1.7.0 | BSD-3-Clause | S | R1-04 | ✅ |
| uvicorn | 0.54.0 | BSD-3-Clause | S | R1-04 | ✅ |
| pydantic | 2.13.5 | MIT | S | R1-04 | ✅ |
| pydantic-settings | 2.15.0 | MIT | S | R1-04 | ✅ |
| structlog | 26.1.0 | MIT OR Apache-2.0 | S | R1-04 | ✅ |
| opentelemetry-api / -sdk / -exporter-otlp-proto-http | 1.45.0 | Apache-2.0 | S | R1-04 | ✅ |
| sqlalchemy | 2.1.1 | MIT | S | R1-05 | ✅ |
| alembic | 1.20.0 | MIT | S | R1-05 | ✅ |
| **psycopg / psycopg-binary / psycopg-pool** | 3.3.6 / 3.3.6 / 3.3.3 | **LGPL-3.0-only** | S | R1-05 | ❗ F1 |
| asyncpg (alternative driver) | 0.31.0 | Apache-2.0 | S | R1-05 | ✅ |
| pgvector (Python client) | 0.5.0 | MIT | S | R1-13 | ✅ |
| argon2-cffi | 25.1.0 | MIT | S | R1-06 | ✅ |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | S | R1-06 | ✅ |
| httpx | 0.28.1 | BSD-3-Clause | S | R1-10 | ✅ (1.0 is still a dev pre-release; do not use) |
| tenacity | 9.1.4 | Apache-2.0 | S | R1-10 | ✅ |
| python-multipart | 0.0.32 | Apache-2.0 | S | R1-11 | ✅ |
| pypdfium2 | 5.13.0 | Apache-2.0 OR BSD-3-Clause (+ bundled library notices) | S | R1-12 | ⚠️ F4 |
| pdfplumber | 0.11.10 | MIT | S | R1-12 | ✅ |
| pdfminer.six | 20260107 | MIT | S | R1-12 | ✅ |
| python-docx | 1.2.0 | MIT | S | R1-12 | ✅ |
| openpyxl | 3.1.5 | MIT | S | R1-31 | ✅ |
| sse-starlette | 3.5.0 | BSD-3-Clause | S | R1-30 | ✅ |
| mcp | 2.2.0 | MIT | S | R1-43 | ✅ (2.3.0 becomes eligible 2026-10-16) |
| dbos | 3.1.0 | MIT (depends on psycopg → F1) | S | R1-21 (spike) | ⚠️ F1 |
| langgraph | 1.2.12 | MIT | S | R1-22 (spike) | ✅ |
| langgraph-checkpoint-postgres | 3.1.2 | MIT (depends on psycopg → F1) | S | R1-22 (spike) | ⚠️ F1 |
| procrastinate (custom-engine queue option) | 3.10.0 | MIT (depends on psycopg → F1) | S | R1-23 (spike) | ⚠️ F1 |
| pytest | 9.1.1 | MIT | D | R1-01 | ✅ |
| pytest-asyncio | 1.4.0 | Apache-2.0 | D | R1-04 | ✅ |
| pytest-cov | 7.1.0 | MIT | D | R1-09 | ✅ |
| hypothesis | 6.168.3 | MPL-2.0 | D | R1-05 | ✅ |
| testcontainers | 4.15.0 | Apache-2.0 | D | R1-05 | ✅ |
| schemathesis | 4.28.0 | MIT | D | R1-45 | ✅ |
| import-linter | 2.15 | BSD-2-Clause | D | R1-04 | ✅ |
| pip-audit | 2.10.1 | Apache-2.0 | D | R1-09 | ✅ |
| ruff | 0.16.9 | MIT | D | R1-01 | ✅ |
| pyright | 1.1.414 | MIT | D | R1-01 | ✅ |
| pre-commit | 4.6.2 | MIT | D | R1-01 | ✅ |

Dropped from the plan: `python-magic` (needs the system `libmagic`; R1-11 checks magic bytes for
the few formats it accepts directly).

## npm packages

| Package | Version (≤ cutoff) | License | Scope | Task | Status |
|---|---|---|---|---|---|
| next | 16.3.6 | MIT | S | R1-08 | ✅ |
| react / react-dom | 19.3.0 | MIT | S | R1-08 | ✅ |
| tailwindcss | 4.3.3 | MIT | S | R1-08 | ✅ |
| @radix-ui/* (via shadcn/ui, copied-in components) | 1.1.23 | MIT | S | R1-08 | ✅ |
| class-variance-authority | 0.7.1 | Apache-2.0 | S | R1-08 | ✅ |
| clsx | 2.1.1 | MIT | S | R1-08 | ✅ |
| tailwind-merge | 3.7.0 | MIT | S | R1-08 | ✅ |
| lucide-react | 1.48.0 | ISC | S | R1-08 | ✅ |
| @tanstack/react-query | 5.104.0 | MIT | S | R1-08 | ✅ |
| openapi-fetch | 0.17.0 | MIT | S | R1-08 | ✅ |
| zod | 4.6.5 | MIT | S | R1-08 | ✅ |
| react-markdown | 10.1.0 | MIT | S | R1-08 | ✅ |
| rehype-sanitize | 6.0.0 | MIT | S | R1-08 | ✅ |
| openapi-typescript | 7.13.0 | MIT | D | R1-08 | ✅ |
| typescript | 7.0.2 | Apache-2.0 | D | R1-08 | ✅ |
| eslint | 10.11.0 | MIT | D | R1-08 | ✅ |
| prettier | 3.9.9 | MIT | D | R1-01 | ✅ |
| @playwright/test | 1.63.0 | Apache-2.0 | D | R1-18 | ✅ |
| axe-core / @axe-core/playwright | 4.13.0 | MPL-2.0 | D | R1-18 | ✅ |
| mermaid / jsdom | 12.0.0 / 30.1.1 | MIT | D | R1-01 | ✅ (transitive F2, F3) |
| @modelcontextprotocol/inspector | 2.8.0 | Apache-2.0 / MIT (relicensing) | D | R1-43 | ✅ F5 (run with `npx`, not a dependency) |
| @xyflow/react | 12.12.0 | MIT | S | R2 | ✅ (not in R1) |

## Services, images, and tools (not Python/npm packages)

| Component | License | Source | Status |
|---|---|---|---|
| PostgreSQL | PostgreSQL License | postgresql.org | ✅ |
| pgvector extension | PostgreSQL License (LICENSE file text) | github.com/pgvector/pgvector | ✅ |
| Ollama | MIT | github.com/ollama/ollama | ✅ (separate process) |
| gitleaks | MIT | github.com/gitleaks/gitleaks | ✅ (dev) |
| uv | MIT OR Apache-2.0 | github.com/astral-sh/uv | ✅ (dev) |
| Node.js | MIT | nodejs.org | ✅ (dev; the Next.js server runs on it) |

Container base images (Debian-based Postgres and Python images) contain OS packages under many
licenses, including GPL. Shipping them unmodified, as separate programs, is aggregation; the
image SBOM (task R1-09) lists them.

## Models

Verified against Hugging Face model cards; all tags verified to exist in the Ollama registry.
Registry: [`config/models.yaml`](../../config/models.yaml).

| Ollama tag | Upstream | License | Download | Tier |
|---|---|---|---|---|
| qwen3:4b (proposed default) | Qwen/Qwen3-4B | Apache-2.0 | 2.50 GB | CPU |
| granite4:micro | ibm-granite/granite-4.0-micro | Apache-2.0 | 2.10 GB | CPU |
| phi4-mini:3.8b | microsoft/Phi-4-mini-instruct | MIT | 2.49 GB | CPU |
| qwen3.5:4b | Qwen/Qwen3.5-4B | Apache-2.0 | 3.32 GB | CPU |
| qwen3:8b | Qwen/Qwen3-8B | Apache-2.0 | 5.23 GB | GPU / 32 GB |
| qwen3.5:9b | Qwen/Qwen3.5-9B | Apache-2.0 | 6.55 GB | GPU / 32 GB |
| granite4:tiny-h | ibm-granite/granite-4.0-h-tiny | Apache-2.0 | 4.23 GB | GPU / 32 GB |
| nomic-embed-text:v1.5 (proposed default) | nomic-ai/nomic-embed-text-v1.5 | Apache-2.0 | 0.27 GB | embedding |
| qwen3-embedding:0.6b | Qwen/Qwen3-Embedding-0.6B | Apache-2.0 | 0.64 GB | embedding |
| bge-m3 | BAAI/bge-m3 | MIT | 1.16 GB | embedding |

This closes the Granite 4.x and Qwen3.5 `[VERIFY]` items: both are Apache-2.0 on their official
model cards (previously known only from secondary reports).

## Exclusions (unchanged)

PyMuPDF (AGPL/commercial), MinIO (AGPL), Redis ≥ 7.4 (RSAL/SSPL/AGPL), and Llama/Gemma as
defaults remain excluded. The license gate now enforces the package-level exclusions
automatically.

## Still open

- **F1** decision (ADR-0013).
- DBOS Conductor and LangGraph Platform terms: not needed, because neither is used. Confirmed
  during the engine spikes (R1-21, R1-22) that the libraries work without them.
- `THIRD_PARTY_LICENSES.md` is generated at release time (R1-50) from the lockfiles, including
  the F4 notices.
