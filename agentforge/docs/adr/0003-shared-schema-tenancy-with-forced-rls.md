# ADR-0003: Shared-schema multi-tenancy with forced RLS, and an engine schema holding IDs only

- **Status:** Accepted (amended in architecture v0.2 to add the engine schema rule)
- **Date:** 2026-10-12

## Context

Several organisations may share one instance, now or in a hosted R3. A missed `WHERE org_id =`
in one query must not leak another tenant's documents. Schema-per-tenant or
database-per-tenant multiply migrations and connections, which is too heavy for a laptop.

## Decision

- Every tenant table in the `app` schema has an `org_id` column and **forced row-level
  security**. Policies compare `org_id` with `current_setting('app.org_id')`, which
  `tenant_session` sets per transaction (`SET LOCAL`), so it cannot leak into the next one.
- **Three database roles:** the *owner* runs migrations; *agentforge_app* (API and worker) is
  subject to RLS and cannot disable it; *agentforge_engine* (the run engine) reaches only the
  `engine` schema.
- The run engine's checkpoints in `engine` hold **references only**: run and item IDs, never
  document text, answers or prompts (gate H6, tested in the engine conformance suite).
- A **schema lint** runs in the tests and fails on any `app` table without forced RLS or an
  `org_id` column.

## Consequences

- Tenant isolation does not depend on every query being written correctly. Tests show zero rows
  without a tenant context, and that writes into another tenant fail (`test_tenancy_rls.py`).
  Every feature area has a cross-organisation test (baseline S4).
- A superuser bypasses RLS. In the dev container the schema owner is a superuser, but the app
  never connects as it. Production must use a non-superuser owner.
- Cross-tenant queries (for operators) need a separate, audited path; none exists in R1.
