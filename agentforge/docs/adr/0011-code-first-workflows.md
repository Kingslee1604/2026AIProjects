# ADR-0011: Code-first workflows in R1, a declarative spec in R2, a constrained visual editor in R3

- **Status:** Accepted (owner decision D1, 2026-10-12)
- **Date:** 2026-10-12

## Context

Workflow builders are attractive, but a visual editor before the execution model is proven
would freeze the wrong abstractions. R1 has exactly two templates: grounded Q&A, and the
security questionnaire (plan → answer each item → review → export).

## Decision

- **R1:** workflows are Python, a small step library (`QuestionnaireSteps`) run by the durable
  engine (ADR-0002). Agents are declared in versioned TOML (prompt, model, limits); their
  behaviour lives in code.
- **R2:** a declarative workflow spec, and a read-only diagram of a run.
- **R3:** a constrained visual editor that produces the same spec.

## Consequences

- R1 is easy to test and to reason about: one workflow, durable and covered by chaos tests
  (R1-36).
- New templates need code in R1. That is acceptable while there are two.
- The R2 spec can be derived from what R1's two templates actually needed.
