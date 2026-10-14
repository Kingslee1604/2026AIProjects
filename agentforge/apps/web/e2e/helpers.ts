import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export const email = process.env.E2E_EMAIL ?? "";
export const password = process.env.E2E_PASSWORD ?? "";

export function requireCredentials(): void {
  if (!email || !password) throw new Error("Set E2E_EMAIL and E2E_PASSWORD (make e2e does this)");
}

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
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

export const POLICY = `# Information Security Policy

## Logging

Access logs are retained for ninety days. Logs are written to immutable storage and reviewed
weekly by the security team.

## Encryption

Customer data at rest is encrypted with AES-256. Encryption keys are rotated every twelve months.
`;
