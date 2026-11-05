import { expect, test, type Page } from "@playwright/test";

import { violations } from "../lib/a11y";
import { createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { issueRental, startIssueInspection } from "../lib/mobile";

// Audit item 44: the main screens checked by axe-core against WCAG 2.1 A and AA (contrast, names
// of controls, labels of fields, structure) in the light and the dark theme of the device.

async function checkScreen(page: Page, name: string) {
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    expect.soft(await violations(page), `${name}, ${colorScheme} theme`).toEqual([]);
  }
}

test("the sign-in screen meets WCAG 2.1 AA", async ({ page }) => {
  await waitForServer(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await checkScreen(page, "login");
});

test("the screens of the company meet WCAG 2.1 AA", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("a11y"), "Анна", "Петрова");
  const s = await createCompany(page);
  const rental = await issueRental(page, s);

  const screens: [string, string][] = [
    ["company", s.base],
    ["rentals", `${s.base}/rentals`],
    ["rental", `${s.base}/rentals/${rental}`],
    ["issue", `${s.base}/rentals/new`],
    ["booking", `${s.base}/rentals/book`],
    ["vehicles", `${s.base}/vehicles`],
    ["clients", `${s.base}/clients`],
    ["finance", `${s.base}/finance`],
    ["settings", `${s.base}/settings`],
    ["profile", "/profile"],
    ["companies", "/companies"],
  ];
  for (const [name, path] of screens) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await checkScreen(page, name);
  }

  // The inspection with the scheme of damages.
  await startIssueInspection(page, s, rental);
  await checkScreen(page, "inspection");
});
