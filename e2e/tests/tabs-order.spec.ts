import { expect, test } from "@playwright/test";

import { createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";
import { doneButton, dragTab, expectOrdering, orderButton, tabNames, tabOf } from "../lib/tabs";

// Customer's decisions of 17.11.2026: a user puts the tabs of any section in their own order.
// The order is changed only in its mode — the button at the right end of the tabs switches it
// on, the tabs sway — by dragging a tab to its place; it is remembered for the user.

const rental = (keys: string[]) => keys.map((key) => t(`rentals.tab_${key}`));

test("the tabs of a section are put in the user's order in the mode of the order", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tabs"), "Анна", "Петрова");
  const s = await createCompany(page);
  await page.goto(`${s.base}/rentals`);
  await expect(tabOf(page, t("rentals.tab_park"))).toBeVisible();
  const usual = (await tabNames(page)).filter((name) => name !== t("rentals.tab_trips"));
  expect(usual).toEqual(rental(["rentals", "schedule", "services", "park", "catalog"]));
  const names = async () => (await tabNames(page)).filter((name) => name !== t("rentals.tab_trips"));

  // Outside the mode a tab is not dragged.
  await dragTab(page, t("rentals.tab_park"), t("rentals.tab_schedule"), "before");
  expect(await names()).toEqual(usual);

  // The mode: the tabs sway, a press does not open a tab, a tab is dragged to its place and the
  // others make room.
  await orderButton(page).click();
  await expectOrdering(page, true);
  expect(await tabOf(page, t("rentals.tab_park")).evaluate((e) => getComputedStyle(e).animationName)).toBe("tab-sway");
  // (A swaying tab never stands still for the test: it is pressed as it is.)
  await tabOf(page, t("rentals.tab_catalog")).click({ force: true });
  await expect(tabOf(page, t("rentals.tab_rentals"))).toHaveAttribute("aria-selected", "true");
  await dragTab(page, t("rentals.tab_park"), t("rentals.tab_schedule"), "before");
  const moved = rental(["rentals", "park", "schedule", "services", "catalog"]);
  await expect.poll(names).toEqual(moved);
  // The arrows move the tab that has the focus.
  await tabOf(page, t("rentals.tab_services")).focus();
  await page.keyboard.press("ArrowLeft");
  const twice = rental(["rentals", "park", "services", "schedule", "catalog"]);
  await expect.poll(names).toEqual(twice);

  // The park is put first.
  await dragTab(page, t("rentals.tab_park"), t("rentals.tab_rentals"), "before");
  const thrice = rental(["park", "rentals", "services", "schedule", "catalog"]);
  await expect.poll(names).toEqual(thrice);

  // Out of the mode the tabs open again.
  await doneButton(page).click();
  await expectOrdering(page, false);
  await tabOf(page, t("rentals.tab_catalog")).click();
  await expect(page).toHaveURL(/tab=catalog/);
  // The section opens on the tab put first — from the menu and by its own address; the other
  // tabs are opened as before.
  await page.getByRole("link", { name: t("nav.rentals"), exact: true }).click();
  await expect(tabOf(page, t("rentals.tab_park"))).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("link", { name: t("vehicles.create_button") })).toBeVisible();
  await tabOf(page, t("rentals.tab_rentals")).click();
  await expect(tabOf(page, t("rentals.tab_rentals"))).toHaveAttribute("aria-selected", "true");
  await page.goto(`${s.base}/rentals`);
  await expect(tabOf(page, t("rentals.tab_park"))).toHaveAttribute("aria-selected", "true");
  await expect.poll(names).toEqual(thrice);

  // Another section has its own order; «Escape» leaves the mode.
  await page.goto(`${s.base}/finance`);
  const money = t("money.tab_money");
  await expect(tabOf(page, money)).toBeVisible();
  await orderButton(page).click();
  await dragTab(page, t("nav.orders"), money, "before");
  await expect.poll(async () => (await tabNames(page)).slice(0, 2)).toEqual([t("nav.orders"), money]);
  await page.keyboard.press("Escape");
  await expectOrdering(page, false);
  // The same order on the other pages of the section and after the page is opened again.
  await tabOf(page, t("nav.orders")).click();
  await expect(page).toHaveURL(/\/orders$/);
  await expect.poll(async () => (await tabNames(page)).slice(0, 2)).toEqual([t("nav.orders"), money]);
  await page.reload();
  await expect(tabOf(page, money)).toBeVisible();
  await expect.poll(async () => (await tabNames(page)).slice(0, 2)).toEqual([t("nav.orders"), money]);
  await page.goto(`${s.base}/rentals`);
  await expect(tabOf(page, t("rentals.tab_park"))).toBeVisible();
  await expect.poll(names).toEqual(thrice);
  // The item of the menu leads to the page of the tab put first.
  await page.getByRole("link", { name: t("nav.finance"), exact: true }).click();
  await expect(page).toHaveURL(/\/orders$/);
  await expect(tabOf(page, t("nav.orders"))).toHaveAttribute("aria-selected", "true");

  // The tabs of a card and of the company are ordered the same way.
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await expect(tabOf(page, t("vehicles.card_main"))).toBeVisible();
  await orderButton(page).click();
  await dragTab(page, t("vehicles.card_ownership"), t("vehicles.card_main"), "before");
  await expect.poll(async () => (await tabNames(page))[0]).toBe(t("vehicles.card_ownership"));
  // The card opens on its first tab too.
  await page.keyboard.press("Escape");
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await expect(tabOf(page, t("vehicles.card_ownership"))).toHaveAttribute("aria-selected", "true");
  await page.goto(`${s.base}/company`);
  await expect(tabOf(page, t("company.tab_about"))).toBeVisible();
  await orderButton(page).click();
  await dragTab(page, t("company.tab_contacts"), t("company.tab_about"), "before");
  await expect.poll(async () => (await tabNames(page))[0]).toBe(t("company.tab_contacts"));
  await page.keyboard.press("Escape");
  await page.goto(`${s.base}/rentals`);
  await page.getByRole("link", { name: "Тест Аренда", exact: true }).click();
  await expect(page).toHaveURL(/\/clients$/);
});
