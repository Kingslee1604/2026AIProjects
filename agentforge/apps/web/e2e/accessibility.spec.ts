import { expect, type Page, test } from "@playwright/test";

import { adminEmail, expectAccessible, requireCredentials, reviewerEmail, signIn } from "./helpers";

test.beforeAll(requireCredentials);

// Every core page, empty or not, in both colour schemes. Pages with data are also checked in
// their own journeys (knowledge, ask, runs, reviews, api-keys).
const SIGNED_OUT = ["/", "/login"];
const SIGNED_IN: { path: string; heading: RegExp }[] = [
  { path: "/app", heading: /./ },
  { path: "/app/knowledge", heading: /Knowledge/ },
  { path: "/app/ask", heading: /Ask/ },
  { path: "/app/runs", heading: /Questionnaires/ },
  { path: "/app/runs/new", heading: /questionnaire/i },
  { path: "/app/reviews", heading: /Review/ },
  { path: "/app/admin", heading: /API keys/ },
];

async function checkPage(page: Page, path: string, heading: RegExp): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
  await expectAccessible(page);
}

test("the first Tab offers to skip the navigation", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/knowledge");
  await expect(page.getByRole("heading", { name: /Knowledge/ }).first()).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport(); // visible while focused
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  await page.keyboard.press("Tab"); // the next stop is in the page, not the navigation
  await expect(page.getByRole("navigation", { name: "Main" }).locator(":focus")).toHaveCount(0);
});

for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`${colorScheme} scheme`, () => {
    test.use({ colorScheme });

    test("signed-out pages pass axe", async ({ page }) => {
      for (const path of SIGNED_OUT) await checkPage(page, path, /./);
    });

    test("signed-in pages pass axe", async ({ page }) => {
      test.setTimeout(90_000); // two sign-ins and seven pages
      // The review queue needs the reviewer permission, which make e2e gives another user.
      await signIn(page, adminEmail);
      for (const { path, heading } of SIGNED_IN.filter((p) => p.path !== "/app/reviews")) {
        await checkPage(page, path, heading);
      }
      await page.context().clearCookies();
      await signIn(page, reviewerEmail);
      await checkPage(page, "/app/reviews", /Review/);
    });
  });
}
