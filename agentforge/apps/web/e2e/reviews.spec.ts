import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import {
  createIndexedKb,
  deleteKb,
  expectAccessible,
  onlyOurKb,
  requireCredentials,
  reviewerEmail,
  signIn,
} from "./helpers";

// Locally the real model answers on a CPU; CI uses the fake one.
test.describe.configure({ mode: "serial", timeout: 420_000 });
test.beforeAll(requireCredentials);

const stamp = Date.now();
const kbName = `E2E review ${stamp}`;
const runName = `E2E review ${stamp}`;
let reviewPath = "";

const QUESTIONNAIRE = [
  "ID,Section,Question,Answer",
  "Q1,Logging,How long are access logs retained?,",
  "Q2,Encryption,How often are encryption keys rotated?,",
  "Q3,Encryption,Is customer data encrypted at rest?,",
].join("\n");
const EDITED = "Keys are rotated every twelve months, or sooner on request.";
const REASON = "Covered by the master agreement.";

test("a member starts a run; reviewing it needs the reviewer permission", async ({ page }) => {
  await signIn(page);
  await createIndexedKb(page, kbName);
  await page.goto("/app/runs/new");
  await page.getByTestId("questionnaire-input").setInputFiles({
    name: "review-questionnaire.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(QUESTIONNAIRE),
  });
  await expect(page.getByTestId("question-preview")).toContainText("encrypted at rest");
  await onlyOurKb(page, kbName, "Answer from");
  await page.getByLabel("Name (optional)").fill(runName);
  await page.getByRole("button", { name: "Start answering" }).click();
  const row = page.getByTestId("run-row").filter({ hasText: runName });
  await expect(row.getByTestId("run-status")).toHaveAttribute("data-status", "waiting_review", {
    timeout: 400_000,
  });

  // The queue lists it with its counts; the member can read the review but not decide.
  await page.getByRole("link", { name: "Reviews" }).click();
  const entry = page.getByTestId("review-row").filter({ hasText: runName });
  await expect(entry).toContainText("3 of 3");
  await entry.getByRole("link", { name: runName }).click();
  await expect(page.getByTestId("review-item")).toBeVisible();
  await expect(page.getByText("Deciding needs the reviewer permission")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Approve/ })).toHaveCount(0);
  reviewPath = new URL(page.url()).pathname;
});

test("a reviewer decides every answer with the keyboard, and the file is ready", async ({
  page,
}) => {
  await signIn(page, reviewerEmail);
  await page.goto(reviewPath);
  const item = page.getByTestId("review-item");
  await expect(item).toContainText("How long are access logs retained?");
  await expectAccessible(page);

  // J and K move between answers.
  await page.keyboard.press("j");
  await expect(item).toContainText("How often are encryption keys rotated?");
  await page.keyboard.press("k");
  await expect(item).toContainText("How long are access logs retained?");

  // A approves question 1, and the next undecided answer opens.
  await page.keyboard.press("a");
  await expect(item).toContainText("How often are encryption keys rotated?");

  // E edits question 2: Ctrl+Enter saves and approves the reviewer's wording.
  await page.keyboard.press("e");
  await expect(page.getByLabel("Your answer")).toBeFocused();
  await page.keyboard.press("Control+A");
  await page.keyboard.type(EDITED);
  await page.keyboard.press("Control+Enter");
  await expect(item).toContainText("Is customer data encrypted at rest?");

  // R rejects question 3 with a reason; Enter confirms.
  await page.keyboard.press("r");
  await expect(page.getByLabel("Why is this answer rejected?")).toBeFocused();
  await page.keyboard.type(REASON);
  await page.keyboard.press("Enter");

  // Every answer decided: focus is on "Complete review", and Enter completes it.
  await expect(page.getByTestId("review-counts")).toContainText(
    "0 to decide · 1 approved · 1 edited · 1 rejected",
  );
  const completeButton = page.getByRole("button", { name: "Complete review" });
  await expect(completeButton).toBeFocused();
  await page.keyboard.press("Enter");

  // The run exports, and the file becomes available here.
  const done = page.getByTestId("review-complete");
  const link = done.getByRole("link", { name: "review-questionnaire - answered.csv" });
  await expect(link).toBeVisible({ timeout: 120_000 });
  await expectAccessible(page);

  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const csv = await readFile(await download.path(), "utf8");
  expect(csv).toContain(EDITED);
  const q3 = csv.split(/\r?\n/).find((line) => line.startsWith("Q3,"));
  expect(q3?.split(",")[3]).toBe(""); // rejected: left empty for a person to fill in
});

test("clean up", async ({ page }) => {
  await signIn(page);
  await deleteKb(page, kbName);
});
