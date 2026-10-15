import { expect, test } from "@playwright/test";

import {
  createIndexedKb,
  deleteKb,
  expectAccessible,
  onlyOurKb,
  requireCredentials,
  signIn,
} from "./helpers";

// Locally the real model answers on a CPU (planning plus ~40 s per question); CI uses the fake.
test.describe.configure({ mode: "serial", timeout: 420_000 });
test.beforeAll(requireCredentials);

const stamp = Date.now();
const kbName = `E2E runs ${stamp}`;
const runName = `E2E questionnaire ${stamp}`;

// The customer's file: an ID, a section, the question, an empty answer column, and their notes.
const QUESTIONNAIRE = [
  "ID,Section,Question,Answer,Notes",
  "Q1,Logging,How long are access logs retained?,,keep",
  "Q2,Encryption,How often are encryption keys rotated?,,keep",
].join("\n");

test("start a questionnaire run and follow its status in the list", async ({ page }) => {
  await signIn(page);
  await createIndexedKb(page, kbName);

  await page.getByRole("link", { name: "Questionnaires" }).click();
  await expect(page.getByRole("heading", { name: "Questionnaires" })).toBeVisible();
  await page.getByRole("link", { name: "New questionnaire" }).click();
  await expect(page.getByRole("heading", { name: "New questionnaire" })).toBeVisible();
  await expectAccessible(page);

  // 1. Upload: the detected columns and the questions are shown.
  await page.getByTestId("questionnaire-input").setInputFiles({
    name: "vendor-questionnaire.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(QUESTIONNAIRE),
  });
  await expect(page.getByText("vendor-questionnaire.csv")).toBeVisible();
  const preview = page.getByTestId("question-preview");
  await expect(preview).toContainText("How long are access logs retained?");
  await expect(preview).toContainText("How often are encryption keys rotated?");
  await expect(page.getByLabel("Questions", { exact: true })).toHaveValue("C");
  await expect(page.getByLabel("Write answers into")).toHaveValue("D");

  // 2. Choose a column yourself: answers into a new column instead of D, saved on the server.
  await page.getByLabel("Write answers into").selectOption("");
  await page.getByRole("button", { name: "Use these columns" }).click();
  await expect(page.getByRole("button", { name: "Use these columns" })).toBeHidden();
  await expect(page.getByLabel("Write answers into")).toHaveValue("");
  await expectAccessible(page);

  // 3. Start from our knowledge base only, with a name to find it by.
  await onlyOurKb(page, kbName, "Answer from");
  await page.getByLabel("Name (optional)").fill(runName);
  await page.getByRole("button", { name: "Start answering" }).click();

  // The list shows the run, and its status moves on without reloading the page.
  await expect(page).toHaveURL(/\/app\/runs$/);
  const row = page.getByTestId("run-row").filter({ hasText: runName });
  await expect(row).toBeVisible();
  await expect(row.getByTestId("run-status")).toHaveAttribute(
    "data-status",
    /queued|running|waiting_review/,
  );
  await expect(row.getByTestId("run-status")).toHaveAttribute("data-status", "waiting_review", {
    timeout: 400_000,
  });
  await expect(row).toContainText("2 of 2");
  await expectAccessible(page);

  // Filters.
  await page.getByRole("button", { name: /^Needs review/ }).click();
  await expect(row).toBeVisible();
  await page.getByRole("button", { name: /^Done/ }).click();
  await expect(row).toBeHidden();
  await page.getByRole("button", { name: /^All/ }).click();

  // The run's own page.
  await row.getByRole("link", { name: runName }).click();
  await expect(page.getByRole("heading", { name: runName })).toBeVisible();
  await expect(page.getByText("2 of 2 questions answered")).toBeVisible();
  await expectAccessible(page);

  await deleteKb(page, kbName);
});
