import { type APIRequestContext, expect, request, test } from "@playwright/test";

import { adminEmail, createIndexedKb, deleteKb, requireCredentials, signIn } from "./helpers";

test.beforeAll(requireCredentials);

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

type JsonRpcResult = Record<string, unknown> & {
  tools?: { name: string }[];
  structuredContent?: { passages: { document: string; content: string }[] };
};

/** One JSON-RPC call to /mcp, as an MCP client would send it (the reply is one SSE event). */
async function mcpCall(
  program: APIRequestContext,
  key: string,
  method: string,
  params: Record<string, unknown> = {},
): Promise<{ status: number; result?: JsonRpcResult }> {
  const response = await program.post("/api/mcp", {
    headers: {
      Authorization: `Bearer ${key}`,
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-11-25",
    },
    data: { jsonrpc: "2.0", id: 1, method, params },
  });
  if (!response.ok()) return { status: response.status() };
  const data = (await response.text())
    .split(/\r?\n/)
    .find((line) => line.startsWith("data: "))
    ?.slice("data: ".length);
  return { status: response.status(), result: JSON.parse(data ?? "{}").result };
}

test("an admin gives an AI assistant a key, and it searches the documents over MCP", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const kbName = `E2E MCP ${Date.now()}`;
  await signIn(page, adminEmail);
  await createIndexedKb(page, kbName);
  const kbs = (await (await page.request.get("/api/v1/knowledge-bases")).json()) as {
    id: string;
    name: string;
  }[];
  const kbId = kbs.find((kb) => kb.name === kbName)?.id;
  expect(kbId).toBeTruthy();

  // A key for AI assistants only.
  await page.goto("/app/admin");
  const keyName = `E2E MCP key ${Date.now()}`;
  await page.getByLabel("Name").fill(keyName);
  await page.getByRole("checkbox", { name: /Read/ }).uncheck();
  await page.getByRole("checkbox", { name: /AI assistants \(MCP\)/ }).check();
  await page.getByRole("button", { name: "Create key" }).click();
  const key = (await page.getByTestId("new-key-value").innerText()).trim();
  await page.getByRole("button", { name: "Done" }).click();

  // The assistant: no browser session, only the key.
  const program = await request.newContext({ baseURL });
  const init = await mcpCall(program, key, "initialize", {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: { name: "e2e-assistant", version: "1.0" },
  });
  expect(init.status).toBe(200);
  const listed = await mcpCall(program, key, "tools/list");
  expect(listed.result?.tools?.map((t) => t.name)).toEqual([
    "search_knowledge",
    "answer_with_citations",
    "get_run",
  ]);
  const found = await mcpCall(program, key, "tools/call", {
    name: "search_knowledge",
    arguments: { query: "How long are access logs retained?", knowledge_base_ids: [kbId] },
  });
  const top = found.result?.structuredContent?.passages[0];
  expect(top?.document).toBe("security-policy.md");
  expect(top?.content).toContain("ninety days");

  // The key opens MCP only: not the REST API. A browser page cannot use it (foreign Origin).
  const auth = { Authorization: `Bearer ${key}` };
  expect((await program.get("/api/v1/knowledge-bases", { headers: auth })).status()).toBe(403);
  const fromPage = await program.post("/api/mcp", {
    headers: {
      ...auth,
      Origin: "https://evil.example",
      Accept: "application/json, text/event-stream",
    },
    data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
  });
  expect(fromPage.status()).toBe(403);

  // Revoked: the assistant is cut off.
  await page.reload();
  const row = page.getByTestId("api-key-row").filter({ hasText: keyName });
  await row.getByRole("button", { name: "Revoke" }).click();
  await row.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(row).toContainText("Revoked");
  expect((await mcpCall(program, key, "tools/list")).status).toBe(401);
  await program.dispose();

  await deleteKb(page, kbName);
});
