import { expect, test } from "@playwright/test";

import { email, password, requireCredentials } from "./helpers";

test.beforeAll(requireCredentials);

test("signed-out visitors are sent to the sign-in page", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in to AgentForge" })).toBeVisible();
});

test("a wrong password shows an error and no session", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("not the password at all");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Next.js renders its own (empty) route-announcer alert, so target the form's error message.
  const error = page.locator("#login-error");
  await expect(error).toHaveText("Invalid email or password");
  await expect(error).toHaveAttribute("role", "alert");
  await expect(page.getByLabel("Password")).toHaveAttribute("aria-describedby", "login-error");
  await expect(page).toHaveURL(/\/login$/);
});

test("sign in, see the app, sign out", async ({ page, context }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByText(`Signed in as ${email}`)).toBeVisible();

  const cookie = (await context.cookies()).find((c) => c.name === "af_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");

  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login$/);
});
