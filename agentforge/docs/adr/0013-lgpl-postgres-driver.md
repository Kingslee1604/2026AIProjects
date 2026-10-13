# ADR-0013: Allow the LGPL psycopg driver as a named exception

- **Status:** Proposed (needs owner decision)
- **Date:** 2026-10-13
- **Context source:** [License verification 2026-10-13](../licenses/verification-2026-10-13.md), finding F1

## Context

AgentForge is Apache-2.0 and its license gate allows only permissive licenses (plus file-level
MPL-2.0). R1-02 found that **psycopg 3** (`psycopg`, `psycopg-binary`, `psycopg-pool`) is
**LGPL-3.0-only**.

psycopg matters because:

- The design prefers one Postgres driver, psycopg 3 (architecture §0, finding 3).
- **DBOS** and **LangGraph's Postgres checkpointer** require it. Procrastinate (a custom-engine
  queue option) also uses it. So two of the three engine candidates cannot avoid it.

The LGPL lets proprietary or differently licensed programs **use** an LGPL library without
adopting the LGPL, provided the library is used unmodified and users can replace it with their
own version. A Python package installed separately and imported at runtime meets both
conditions. Distributing it (for example inside a container image) additionally requires
shipping its license text and making its source available, which the upstream project already
publishes. Many permissively licensed projects depend on psycopg on these terms. This is a
practical reading, not legal advice.

## Decision (proposed)

1. Allow `psycopg`, `psycopg-binary`, and `psycopg-pool` as **named exceptions** in
   `licenses/policy.toml`, scoped to those three packages only. LGPL stays off the general
   allowlist, so any other LGPL package still fails the gate.
2. Conditions:
   - Use them unmodified, installed as ordinary packages (never vendored or copied into our code).
   - Ship the LGPL text and a source link in `THIRD_PARTY_LICENSES` with every image or release.
3. Keep `asyncpg` (Apache-2.0) as the documented fallback. If the custom engine wins ADR-2,
   reconsider whether one driver can be dropped.

The exceptions are added to `policy.toml` by the task that first installs psycopg (R1-05), and
only after this ADR is accepted.

## Alternatives

| Option | Effect |
|---|---|
| **A. Named exception (proposed)** | All three engine candidates remain possible; one driver |
| B. Ban LGPL entirely, use asyncpg only | Rules out DBOS and the LangGraph checkpointer before the M3 prototypes even run; forces the custom engine |
| C. Allow LGPL generally | Simpler policy, but any future LGPL package would enter without review |

## Consequences

- The engine comparison (ADR-2) stays a fair three-way test.
- One extra notice obligation at release time, already tracked for R1-50.
- Organizations with blanket "no LGPL" policies would see psycopg in the SBOM. The asyncpg
  fallback is documented for that case.
