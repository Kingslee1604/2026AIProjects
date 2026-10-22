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

test("file pickers have one Tab stop, with a clear focus ring", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/runs/new");
  await expect(page.getByRole("heading", { name: "New questionnaire" })).toBeVisible();
  const choose = page.getByRole("button", { name: "Choose file" });
  for (let i = 0; i < 20 && !(await choose.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press("Tab");
  }
  await expect(choose).toBeFocused();
  await expect(choose).toHaveCSS("outline-width", "3px");
  await page.keyboard.press("Tab");
  // The hidden <input type=file> behind the button is not a second, invisible stop.
  await expect(page.getByTestId("questionnaire-input")).not.toBeFocused();
  await expect(page.getByTestId("questionnaire-input")).toHaveAttribute("tabindex", "-1");
});

test("dark by default; the toggle switches to light and is remembered", async ({ page }) => {
  await signIn(page);
  const html = page.locator("html");
  await expect(html).not.toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(html).not.toHaveAttribute("data-theme", "light");
});

// The app's theme (dark by default; the toggle stores "light"), not the OS preference.
for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("agentforge-theme", t), theme);
    });

    test("the page renders in this theme", async ({ page }) => {
      await page.goto("/login");
      const html = page.locator("html");
      if (theme === "light") await expect(html).toHaveAttribute("data-theme", "light");
      else await expect(html).not.toHaveAttribute("data-theme", "light");
    });

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
