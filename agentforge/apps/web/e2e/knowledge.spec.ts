import { expect, test } from "@playwright/test";

import { expectAccessible, POLICY, requireCredentials, signIn } from "./helpers";

test.beforeAll(requireCredentials);

test("create a knowledge base, upload, watch it index, search, and delete", async ({ page }) => {
  test.setTimeout(120_000); // indexing on a CPU-only machine with real models takes a while
  await signIn(page);
  await page.getByRole("link", { name: "Knowledge" }).click();
  await expect(page.getByRole("heading", { name: "Knowledge", exact: true })).toBeVisible();
  await expectAccessible(page);

  const name = `E2E policies ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  await page.getByTestId("file-input").setInputFiles({
    name: "security-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(POLICY),
  });
  const row = page.getByTestId("document-row").filter({ hasText: "security-policy.md" });
  await expect(row).toBeVisible();
  // Live status: the table polls until ingestion settles, without a page reload.
  await expect(row.getByText("Ready")).toBeVisible({ timeout: 90_000 });

  await page.getByLabel("Question").fill("How long are access logs retained?");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const hits = page.getByTestId("search-hit");
  await expect(hits.first()).toBeVisible();
  await expect(page.getByRole("list", { name: "Search results" })).toContainText("ninety days");
  await expectAccessible(page);

  // Delete the document (inline confirmation), then the knowledge base.
  await row.getByRole("button", { name: "Delete" }).click();
  await expect(row.getByRole("button", { name: "Cancel" })).toBeFocused();
  await row.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText("No documents yet.")).toBeVisible();

  await page.getByRole("button", { name: "Delete knowledge base" }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/app\/knowledge$/);
  await expect(page.getByRole("link", { name })).toHaveCount(0);
});

test("a rejected upload explains why", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/knowledge");
  const name = `E2E rejects ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  await page.getByTestId("file-input").setInputFiles({
    name: "program.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("MZ\x90\x00 not a document"),
  });
  await expect(page.getByText(/program\.exe: .+/)).toContainText(/not supported|type/i);

  await page.getByRole("button", { name: "Delete knowledge base" }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/app\/knowledge$/);
});
