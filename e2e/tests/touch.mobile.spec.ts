import { expect, test } from "@playwright/test";

import { violations } from "../lib/a11y";
import { createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";
import { issueRental, sideScroll, smallTargets, startIssueInspection, wideElements } from "../lib/mobile";

// Audit item 45: the main screens on a phone (Android and iPhone projects). No page scrolls
// sideways, and every control is at least 44 px high, as on the inspection page (§ 10.4.1).
// Audit item 44: on the narrow screen too axe-core finds nothing against WCAG 2.1 AA (the name of
// the user is hidden there, the menu button keeps its own).

async function checkScreen(page: import("@playwright/test").Page, name: string) {
  await page.waitForLoadState("networkidle");
  const scroll = await sideScroll(page);
  const wide = scroll > 1 ? (await wideElements(page)).join("; ") : "";
  expect(scroll, `${name}: sideways scroll by ${wide}`).toBeLessThanOrEqual(1);
  expect(await smallTargets(page), `${name}: controls lower than 44 px`).toEqual([]);
  expect.soft(await violations(page), `${name}: WCAG 2.1 AA`).toEqual([]);
}

test("the sign-in screen fits a phone", async ({ page }) => {
  await waitForServer(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await checkScreen(page, "login");
});

test("the screens of the company fit a phone", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("phone"), "Анна", "Петрова");
  const s = await createCompany(page);
  const rental = await issueRental(page, s);

  const screens: [string, string][] = [
    ["company", s.base],
    ["rentals", `${s.base}/rentals`],
    ["rental", `${s.base}/rentals/${rental}`],
    ["issue", `${s.base}/rentals/new`],
    ["vehicles", `${s.base}/vehicles`],
    ["clients", `${s.base}/clients`],
    ["profile", "/profile"],
  ];
  for (const [name, path] of screens) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await checkScreen(page, name);
  }

  // The cards opened from the lists.
  await page.goto(`${s.base}/vehicles`);
  await page.getByRole("link", { name: /Toyota Yaris/ }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Toyota Yaris");
  await checkScreen(page, "vehicle");

  await page.goto(`${s.base}/clients`);
  await page.getByRole("link", { name: /Иван Петров/ }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Иван");
  await checkScreen(page, "client");

  await startIssueInspection(page, s, rental);
  await checkScreen(page, "inspection");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("inspections.kind_issue"));
});
