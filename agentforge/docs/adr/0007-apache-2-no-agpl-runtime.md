# ADR-0007: Apache-2.0, with AGPL runtime dependencies excluded

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

AgentForge should be usable and forkable by companies, whose legal teams typically refuse
copyleft that reaches network services (AGPL) or attaches to linking. The license of every
dependency must be checked, not assumed.

## Decision

- AgentForge is **Apache-2.0** (`LICENSE`, `NOTICE`).
- A **license gate** (`make licenses`, part of `make check` and CI) allows only the licenses in
  `licenses/policy.toml`: permissive licenses, plus MPL-2.0 at file level. Any other license
  fails the build.
- Exceptions are named and decided in an ADR. The only one so far is the LGPL psycopg driver
  (ADR-0013).
- Models are not code dependencies, but their licenses matter too. Defaults use permissively
  licensed weights; models with custom use restrictions (for example Llama and Gemma) can be
  chosen by the user but are not defaults (architecture §8.3).

## Consequences

- Some well-known libraries are unavailable (AGPL or SSPL ones), and alternatives are chosen
  instead.
- Every new dependency needs a license check before it lands; the gate makes that automatic.
- The verification log lives in `docs/licenses/`.
