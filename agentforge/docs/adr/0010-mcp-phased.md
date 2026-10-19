# ADR-0010: MCP in both directions, phased

- **Status:** Accepted (amended in architecture v0.2)
- **Date:** 2026-10-12. The R1 part was implemented 2026-10-19 (R1-43, R1-44).

## Context

The Model Context Protocol lets AI assistants use AgentForge (AgentForge as an MCP **server**),
and lets AgentForge agents use external tools (AgentForge as an MCP **client**). The client
direction is the risky one: tool calls with side effects, tool descriptions that inject
instructions, and server-side request forgery. The specification itself changed incompatibly
in 2026-07-28.

## Decision

- **R1: a read-only server** at `/mcp`. It uses the official `mcp` SDK 2.x over Streamable HTTP,
  is stateless, and targets spec 2026-07-28 while still serving handshake-era (2025-11-25)
  clients. It has three read-only tools in a fixed order (`search_knowledge`,
  `answer_with_citations` with progress, and `get_run`). It accepts API keys with `mcp:use`
  only, validates `Origin`, rate-limits per key, and audits every call
  ([guide](../mcp/README.md)).
- **R2: a governed client.** Every control ships together: tool allowlists per agent version,
  argument schemas, approval for side effects, SSRF protection, and outputs treated as
  untrusted. Nothing ships partially.
- **R3:** stdio transport and OAuth.

## Consequences

- R1 shows MCP working, with a small and safe surface. A snapshot test fails if a tool is added,
  renamed, or not read-only.
- MCP Inspector 2.9.0 lists exactly three tools and gets cited answers
  (`docs/mcp/2026-10-19-inspector.log`).
- SDK and spec churn are contained in one module (`api/mcp_server.py`).
