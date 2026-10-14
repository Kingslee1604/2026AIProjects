import { expect, type Page, test } from "@playwright/test";

import { expectAccessible, POLICY, requireCredentials, signIn } from "./helpers";

// Locally these run against the real model on a CPU (15-60 s per answer); CI uses the fake one.
test.describe.configure({ mode: "serial", timeout: 240_000 });
test.beforeAll(requireCredentials);

let kbName = "";

async function createIndexedKb(page: Page): Promise<void> {
  kbName = `E2E ask ${Date.now()}`;
  await page.goto("/app/knowledge");
  await page.getByLabel("Name").fill(kbName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: kbName })).toBeVisible();
  await page.getByTestId("file-input").setInputFiles({
    name: "security-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(POLICY),
  });
  await expect(page.getByTestId("document-row").getByText("Ready")).toBeVisible({
    timeout: 90_000,
  });
}

/** Search only the test's knowledge base, whatever else the stack contains. */
async function onlyOurKb(page: Page): Promise<void> {
  const group = page.getByRole("group", { name: "Search in" });
  await expect(group.getByRole("checkbox", { name: kbName })).toBeChecked();
  const boxes = group.getByRole("checkbox");
  for (const box of await boxes.all()) {
    const label = (await box.locator("xpath=..").innerText()).trim();
    if (label.startsWith(kbName)) {
      // Toggle off and on: an explicit choice, so knowledge bases that other tests create
      // (or delete) meanwhile are not added to the selection automatically.
      await box.uncheck();
      await box.check();
    } else {
      await box.uncheck();
    }
  }
}

test("ask, see verified sentences, open the cited passage", async ({ page }) => {
  await signIn(page);
  await createIndexedKb(page);

  await page.getByRole("link", { name: "Ask" }).click();
  await expect(page.getByRole("heading", { name: "Ask", exact: true })).toBeVisible();
  await onlyOurKb(page);
  await page.getByLabel("Question").fill("How long are access logs retained?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page).toHaveURL(/\?answer=/);

  const claims = page.getByTestId("claim");
  await expect(claims.first()).toBeVisible({ timeout: 180_000 });
  await expect(claims.first()).toHaveAttribute("data-status", /verified|rescued|partial/);
  await expect(claims.first()).toContainText(/ninety days/i);

  await claims.first().click();
  const source = page.getByTestId("source-panel");
  await expect(source).toBeFocused();
  await expect(source).toContainText("security-policy.md");
  await expect(source.locator("mark")).toContainText(/ninety days/i);

  await page.getByText("How this was answered").click();
  await expect(page.getByText("Draft the answer")).toBeVisible();
  await expectAccessible(page);
});

test("keyboard only: ask with Ctrl+Enter and open a source with Enter", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/ask");
  await onlyOurKb(page);
  const question = page.getByLabel("Question");
  await question.focus();
  await page.keyboard.type("How often are encryption keys rotated?");
  await page.keyboard.press("Control+Enter");
  await expect(page).toHaveURL(/\?answer=/);

  const claim = page.getByTestId("claim").first();
  await expect(claim).toBeVisible({ timeout: 180_000 });
  // Tab from the question box until the first answer sentence has focus.
  await question.focus();
  for (let i = 0; i < 10 && !(await claim.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press("Tab");
  }
  await expect(claim).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("source-panel")).toBeFocused();
  await expect(page.getByTestId("source-panel")).toContainText("security-policy.md");
});

test("an unanswerable question says so instead of guessing", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/ask");
  await onlyOurKb(page);
  // No word in common with the policy: CI's fake model matches by shared words, so this is the
  // case it can recognize as unanswerable; the real model must recognize it too.
  await page.getByLabel("Question").fill("Which pets may employees bring to work?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByTestId("insufficient")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("claim")).toHaveCount(0);
  await expectAccessible(page);

  // Clean up the test knowledge base.
  await page.goto("/app/knowledge");
  await page.getByRole("link", { name: kbName }).click();
  await page.getByRole("button", { name: "Delete knowledge base" }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/app\/knowledge$/);
});
