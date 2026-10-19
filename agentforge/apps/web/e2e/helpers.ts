import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export const email = process.env.E2E_EMAIL ?? "";
export const password = process.env.E2E_PASSWORD ?? "";

export function requireCredentials(): void {
  if (!email || !password || !reviewerEmail || !adminEmail) {
    throw new Error(
      "Set E2E_EMAIL, E2E_REVIEWER_EMAIL, E2E_ADMIN_EMAIL, and E2E_PASSWORD (make e2e does this)",
    );
  }
}

/** Users with more rights (same password; make e2e creates them): may decide reviews, and an
 * admin (API keys). */
export const reviewerEmail = process.env.E2E_REVIEWER_EMAIL ?? "";
export const adminEmail = process.env.E2E_ADMIN_EMAIL ?? "";

export async function signIn(page: Page, as: string = email): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(as);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app$/);
}

/** Fails on serious or critical WCAG 2.2 A/AA violations (the R1 accessibility bar). */
export async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(
    blocking.map(
      (v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`,
    ),
  ).toEqual([]);
}

/** A new knowledge base holding the test policy, once its document is indexed. */
export async function createIndexedKb(page: Page, name: string): Promise<void> {
  await page.goto("/app/knowledge");
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByTestId("file-input").setInputFiles({
    name: "security-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(POLICY),
  });
  await expect(page.getByTestId("document-row").getByText("Ready")).toBeVisible({
    timeout: 90_000,
  });
}

/** Use only the test's knowledge base, whatever else the stack contains. */
export async function onlyOurKb(page: Page, kbName: string, legend: string): Promise<void> {
  const group = page.getByRole("group", { name: legend });
  await expect(group.getByRole("checkbox", { name: kbName })).toBeChecked();
  for (const box of await group.getByRole("checkbox").all()) {
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

export async function deleteKb(page: Page, name: string): Promise<void> {
  await page.goto("/app/knowledge");
  await page.getByRole("link", { name }).click();
  await page.getByRole("button", { name: "Delete knowledge base" }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/app\/knowledge$/);
}

export const POLICY = `# Information Security Policy

## Logging

Access logs are retained for ninety days. Logs are written to immutable storage and reviewed
weekly by the security team.

## Encryption

Customer data at rest is encrypted with AES-256. Encryption keys are rotated every twelve months.
`;
