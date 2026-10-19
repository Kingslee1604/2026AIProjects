# ADR-0009: The public demo is a labelled replay of real runs; a hosted live demo only behind the R2 gate

- **Status:** Accepted (amended in architecture v0.2). In R1 the replay (R1-47) is deferred.
- **Date:** 2026-10-12

## Context

A public live instance would accept uploads and model calls from strangers. That means abuse,
cost, prompt injection at scale, and the full pre-public security gate. The reference machine is
a CPU-only laptop that cannot serve the public. Yet people evaluating the project should see a
real run, not a mock-up.

## Decision

- The public demo is a **static replay** of runs recorded live on the reference machine: their
  events, claims and documents, stamped with hardware, model and date. It carries a permanent
  "Recorded run" banner and makes **no backend calls** (baseline S15).
- Recorded content is only the self-authored, fictional Northwind corpus (CC-BY-4.0).
- A **hosted live** demo needs the R2 hosted-demo gate (architecture §13.7: invite-only and
  time-limited). Open sign-up needs the R3 pre-public gate (§13.8).

## Consequences

- No public attack surface in R1. Anyone can still run the real thing locally in three commands.
- Replay numbers are real, but they come from one machine; every recording carries its
  hardware manifest.
- Until R1-47 ships, the demo is the local quickstart (and the R1-50 video when it is made).
