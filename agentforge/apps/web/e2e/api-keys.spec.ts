import { expect, request, test } from "@playwright/test";

import { adminEmail, expectAccessible, requireCredentials, signIn } from "./helpers";

test.beforeAll(requireCredentials);

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

test("an admin creates a key, a program uses it, and revoking it stops it", async ({ page }) => {
  test.setTimeout(90_000); // several steps, each a round trip on a busy laptop
  await signIn(page, adminEmail);
  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible();

  const name = `E2E key ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await expect(page.getByRole("checkbox", { name: /Read/ })).toBeChecked(); // the default
  await page.getByLabel("Expires after").selectOption("30");
  await page.getByRole("button", { name: "Create key" }).click();
  const shown = page.getByTestId("new-key-value");
  await expect(shown).toBeVisible();
  const key = (await shown.innerText()).trim();
  expect(key).toMatch(/^af_[a-z0-9]{10}_/);
  await expectAccessible(page);
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.getByTestId("new-key")).toBeHidden(); // shown once

  // Another program: no browser session, only the key.
  const program = await request.newContext({ baseURL });
  const auth = { Authorization: `Bearer ${key}` };
  expect((await program.get("/api/v1/knowledge-bases", { headers: auth })).status()).toBe(200);
  const write = await program.post("/api/v1/knowledge-bases", {
    headers: auth,
    data: { name: "not allowed" },
  });
  expect(write.status()).toBe(403); // a read-only key

  await page.reload();
  const row = page.getByTestId("api-key-row").filter({ hasText: name });
  await expect(row).toContainText("Active");
  await expect(row).not.toContainText("never"); // last used: just now
  await row.getByRole("button", { name: "Revoke" }).click();
  await row.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(row).toContainText("Revoked");
  expect((await program.get("/api/v1/knowledge-bases", { headers: auth })).status()).toBe(401);
  await program.dispose();
});

test("members cannot manage API keys", async ({ page }) => {
  await signIn(page);
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
  expect((await page.request.get("/api/v1/api-keys")).status()).toBe(403);
});
