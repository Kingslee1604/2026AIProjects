# ADR-0005: A thin model gateway with schema-constrained output; no LangChain or LiteLLM

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Every model call must return structured data that can be validated, be recorded for audit and
debugging, survive a slow or failing local model, and be replaceable by a deterministic fake
in CI. Large frameworks bring many transitive dependencies, which matters for the license gate
and supply-chain scans, and they hide the prompt and the retry behaviour.

## Decision

`agentforge.models` exposes `generate`, `generate_structured` and `embed` over three providers:

- **ollama**: sends the JSON schema as `format`, with `think: false` and a fallback for models
  that reject it;
- **openai_compatible**: any OpenAI-style endpoint;
- **fake**: deterministic, with configurable latency and seeded fault injection
  (`MODEL_PROVIDER=fake` routes every call to it).

Output is validated with Pydantic. On failure there is **one repair attempt** that includes the
validation error, then the next model in the fallback list, then a retryable error class.
Timeouts and outages are classified. **Exactly one `app.llm_calls` row per provider call**,
under RLS, records the prompt, reply, tokens, latency and error class.

## Consequences

- Prompts are plain, versioned files (`agents/definitions/*.toml`); changing one creates a new
  immutable agent version.
- CI and browser tests need no model (`fake`), and durability tests inject faults from a seed.
- **Measured** (R1-40): 100% of draft and verify calls produced valid structured output on the
  first try with `qwen3:4b`.
- Adding a provider means writing one adapter in `models/providers.py`.
