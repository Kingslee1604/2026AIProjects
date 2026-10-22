# MCP server

AgentForge serves the [Model Context Protocol](https://modelcontextprotocol.io) at `/mcp` on the
API (default `http://127.0.0.1:8000/mcp`), so an AI assistant such as Claude Code, Claude Desktop,
or any MCP client can search your documents and get cited answers. It is **read-only**: no tool
creates, changes, or deletes anything.

## Tools

| Tool | What it does | Typical time (CPU-only laptop) |
|---|---|---|
| `search_knowledge(query, knowledge_base_ids?, limit?)` | Hybrid search; passages with document, pages, section, and a `flagged` mark for documents that contain instructions aimed at AI assistants | under 1 s |
| `answer_with_citations(question, knowledge_base_ids?)` | A grounded answer: every sentence carries the quote that proves it, or `insufficient_evidence` instead of a guess. Sends progress notifications (searching → drafting → checking → done) | 45–70 s |
| `get_run(run_id)` | A questionnaire run: status, progress, each answer and its confidence, answers still to review, answered files | under 1 s |

The list is fixed: always these three, in this order, all annotated `readOnlyHint: true`,
`destructiveHint: false`. Clients may cache the list for five minutes (`ttlMs`). A test fails if a
tool is added, renamed, or not read-only (`tests/integration/test_mcp.py`).

## Connecting

1. An admin creates an API key with the **`mcp:use`** scope (app → Admin → API keys). The key acts
   for the admin who created it, limited to its scopes and to that person's current role.
2. Point the client at `/mcp` with the key as a bearer token. For example, Claude Code:

   ```bash
   claude mcp add --transport http agentforge http://127.0.0.1:8000/mcp \
     --header "Authorization: Bearer af_..."
   ```

   Or the MCP Inspector (CLI):

   ```bash
   npx -y @modelcontextprotocol/inspector@2.9.0 --cli http://127.0.0.1:8000/mcp \
     --transport http --header "Authorization: Bearer af_..." --method tools/list
   ```

Both protocol eras are served: 2026-07-28 clients (stateless requests) and handshake-era
(2025-11-25) clients that start with `initialize`. The server is stateless (no MCP sessions).

**Timeouts.** An answer on a CPU-only machine takes about a minute. Clients that ask for progress
(the Python and TypeScript SDKs do when given a progress callback) receive an update at each stage
and normally reset their timeout on it. The Inspector CLI does not ask for progress and gives up
after 60 s, which a cold model (first question after start-up) can exceed; ask again once the
model is loaded, or use a client with progress.

## Security

| Control | Behaviour |
|---|---|
| Authentication | API keys only (`Authorization: Bearer af_...`). No key, an unknown, revoked, or expired key → 401. A browser session cookie is not accepted |
| Authorization | The key needs `mcp:use` (else 403) and acts within its organization only: other organizations' knowledge bases and runs are "not found" |
| Origin | Requests with an `Origin` header are refused (403) unless it is listed in `AGENTFORGE_MCP_ALLOWED_ORIGINS` (JSON list, default empty). Programs send no `Origin`; browsers always do, which stops a web page from using a key it does not own (DNS rebinding) |
| Rate limits | Per key: `AGENTFORGE_MCP_REQUESTS_PER_MINUTE` (default 60; 429 with `Retry-After`) and `AGENTFORGE_MCP_ANSWERS_PER_MINUTE` (default 6; an error result "Too many answers") |
| Audit | Every tool call is recorded as `mcp.tool_call` (success or failure) with the key, the client's name and version, and the protocol version |
| Untrusted content | Document text is marked untrusted in the tool descriptions and schemas; flagged documents are marked `flagged: true` |
| Errors | Expected failures ("Knowledge base not found", "Run not found") are returned as tool errors; anything else returns "Internal error (trace …)" without details |

## Evidence

[`2026-10-19-inspector.log`](2026-10-19-inspector.log): the official MCP Inspector 2.9.0 (CLI)
against the running stack (real model qwen3:4b on the CPU-only reference laptop), with the keys
redacted and revoked afterwards:

- `tools/list` returns exactly the three tools, read-only, with input and output schemas.
- `search_knowledge` returns the logging passage of `security-policy.md`, p. 1.
- `answer_with_citations` answers "How long are access logs retained?" in 46 s: 3 claims, each
  with its quote (2 verified word for word, 1 found by the AI check).
- The same `tools/list` as a legacy (2025-11-25) client returns the same three names.
- `get_run` returns a questionnaire run with its answers.
- No key → 401; a key without `mcp:use` → 403; a valid key with `Origin: https://evil.example` → 403.

Progress over real HTTP (Python SDK client, same question, same machine):

```text
   0.2s progress 0.0/3.0 Searching the documents
   0.3s progress 1.0/3.0 Drafting an answer from 6 passages
  28.6s progress 2.0/3.0 Checking 3 drafted sentences against their sources
  48.7s progress 3.0/3.0 Done
  48.9s done is_error=False
```

The log shows the answer text "…immutable storage The security team…": the model left one claim
without a full stop. Since then, claims are joined into sentences that always end with one.

The automated equivalents run in CI on every push (`tests/integration/test_mcp.py`, R1-44):
tool-list snapshot, search, answer with progress, `get_run`, legacy client, audit, refused keys,
foreign and allowed Origins, rate limits, and a cross-organization suite.
