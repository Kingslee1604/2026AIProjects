import { expect, type Page, test } from "@playwright/test";

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
let runPath = "";

// The customer's file: an ID, a section, the question, an empty answer column, and their notes.
const QUESTIONNAIRE = [
  "ID,Section,Question,Answer,Notes",
  "Q1,Logging,How long are access logs retained?,,keep",
  "Q2,Encryption,How often are encryption keys rotated?,,keep",
].join("\n");

const ANSWER_TIMEOUT = 400_000;

async function eventSeqs(page: Page): Promise<number[]> {
  const seqs = await page
    .getByTestId("run-events")
    .getByTestId("event")
    .evaluateAll((nodes) => nodes.map((n) => Number(n.getAttribute("data-seq"))));
  return seqs;
}

/** Ask the API directly (not through the page), e.g. while the page is offline. */
async function apiPost(page: Page, path: string): Promise<number> {
  const session = await page.request.get("/api/v1/auth/session");
  const { csrf_token: csrf } = (await session.json()) as { csrf_token: string };
  const response = await page.request.post(`/api${path}`, { headers: { "X-CSRF-Token": csrf } });
  return response.status();
}

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
    timeout: ANSWER_TIMEOUT,
  });
  await expect(row).toContainText("2 of 2");
  await expectAccessible(page);

  // Filters.
  await page.getByRole("button", { name: /^Needs review/ }).click();
  await expect(row).toBeVisible();
  await page.getByRole("button", { name: /^Done/ }).click();
  await expect(row).toBeHidden();
  await page.getByRole("button", { name: /^All/ }).click();

  await row.getByRole("link", { name: runName }).click();
  await expect(page.getByRole("heading", { name: runName })).toBeVisible();
  runPath = new URL(page.url()).pathname;
});

test("the run page: steps, model calls, retrievals, and a live retry", async ({ page }) => {
  await signIn(page);
  await page.goto(runPath);
  await expect(page.getByTestId("live-state")).toHaveAttribute("data-state", "live");
  await expect(page.getByText("Model calls", { exact: true }).first()).toBeVisible(); // budget
  const lanes = page.getByTestId("item-lane");
  await expect(lanes).toHaveCount(2);
  await expect(page.getByTestId("run-events")).toContainText("Waiting for review");
  // Nothing below may reload the page: this marker would be lost.
  await page.evaluate(() => {
    (window as unknown as { stayed: boolean }).stayed = true;
  });

  // Open a question: its model calls (with the prompt) and the passages retrieved.
  const first = lanes.filter({ hasText: "access logs" });
  await first.getByRole("button", { name: /access logs/ }).click();
  const panel = page.getByTestId("step-panel");
  await expect(panel.getByRole("heading", { name: "Question 1" })).toBeVisible();
  await expect(panel).toContainText("How long are access logs retained?"); // Input tab
  await panel.getByRole("tab", { name: "Model calls" }).click();
  const draft = panel.getByTestId("model-call").filter({ hasText: "Draft the answer" });
  await expect(draft.first()).toBeVisible();
  await draft.first().getByText("Prompt and reply").click();
  await expect(draft.first()).toContainText("reply");
  await panel.getByRole("tab", { name: "Retrievals" }).click();
  await expect(panel.getByTestId("retrieval").first()).toContainText("security-policy.md");
  await expectAccessible(page);

  // Retry from the UI: the question is answered again, live.
  await panel.getByRole("button", { name: "Answer again" }).click();
  const events = page.getByTestId("run-events");
  await expect(events).toContainText("Asked to answer again");
  await expect(events.getByTestId("event").filter({ hasText: /Answered.*retry 1/ })).toBeVisible({
    timeout: ANSWER_TIMEOUT,
  });
  await expect(first).toHaveAttribute("data-status", /answered|insufficient_evidence/);
  expect(await page.evaluate(() => (window as unknown as { stayed?: boolean }).stayed)).toBe(true);
});

test("a dropped connection resumes with no gaps or duplicates", async ({ page, context }) => {
  await signIn(page);
  await page.goto(runPath);
  const live = page.getByTestId("live-state");
  await expect(live).toHaveAttribute("data-state", "live");
  await expect(page.getByTestId("run-events")).toContainText("retry 1");

  // Drop the connection, and make progress while the page is offline.
  await context.setOffline(true);
  await expect(live).toHaveAttribute("data-state", "reconnecting");
  const runId = runPath.split("/").at(-1);
  expect(await apiPost(page, `/v1/runs/${runId}/items/1/retry`)).toBe(202);
  await context.setOffline(false);

  // Back online: the stream resumes after the last event it had, and catches up.
  await expect(live).toHaveAttribute("data-state", "live", { timeout: 30_000 });
  await expect(
    page
      .getByTestId("run-events")
      .getByTestId("event")
      .filter({ hasText: /Answered.*retry 2/ }),
  ).toBeVisible({ timeout: ANSWER_TIMEOUT });
  const shown = await eventSeqs(page);
  const stored = (await (await page.request.get(`/api/v1/runs/${runId}/events`)).json()) as {
    seq: number;
  }[];
  expect(shown).toEqual(stored.map((e) => e.seq)); // every event, once, in order
});

test("cancel from the run page", async ({ page }) => {
  await signIn(page);
  await page.goto(runPath);
  await page.getByRole("button", { name: "Cancel run" }).click();
  await page
    .getByRole("group", { name: "Stop answering this questionnaire?" })
    .getByRole("button", { name: "Cancel run" })
    .click();
  await expect(page.getByTestId("run-status").first()).toHaveAttribute("data-status", "cancelled");
  await expect(page.getByTestId("live-state")).toHaveAttribute("data-state", "ended");
  await expect(page.getByTestId("run-events")).toContainText("Cancelled");
  await expectAccessible(page);

  await deleteKb(page, kbName);
});
