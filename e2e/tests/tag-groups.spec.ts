import { expect, test } from "@playwright/test";

import { createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decisions of 17.11.2026: tags have groups. A group names the employees who make
// its tags and the kinds of records its tags are offered for; everybody sees the groups.
test("a group of tags names its makers and where its tags are offered", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tag-groups"), "Анна", "Петрова");
  const s = await createCompany(page);
  const dialog = page.getByRole("dialog");
  const save = () => dialog.getByRole("button", { name: t("common.save"), exact: true }).click();

  // A group for the items of the warehouse, with an employee named in it.
  await page.goto(`${s.base}/company/tags`);
  await page.getByRole("button", { name: t("tags.group_new"), exact: true }).click();
  await dialog.getByLabel(t("tags.group_name")).fill("Категории запчастей");
  // The explanation under «i» is seen whole: inside the screen, not cut by the window.
  await dialog.getByRole("note").first().hover();
  const pop = dialog.locator(".info-pop");
  await expect(pop).toHaveText(t("tags.group_kinds_hint"));
  const box = await pop.boundingBox();
  const screen = page.viewportSize()!;
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(screen.width);
  expect(box!.y + box!.height).toBeLessThanOrEqual(screen.height);
  await page.mouse.move(0, 0);
  await expect(pop).toHaveCount(0);
  await dialog.getByRole("checkbox", { name: t("search.kind_item"), exact: true }).check();
  await dialog.getByRole("checkbox", { name: /Петрова/ }).check();
  await save();
  await expect(dialog).toHaveCount(0);
  const group = page.locator("section.tag-group", { hasText: "Категории запчастей" });
  await expect(group).toContainText(t("tags.group_where", { kinds: t("search.kind_item") }));
  await expect(group).toContainText("Петрова");

  // A tag made in the group, and one of no group.
  await group.getByRole("button", { name: t("tags.add_to_group", { group: "Категории запчастей" }), exact: true }).click();
  await dialog.getByLabel(t("tags.name")).fill("Тормоза");
  await expect(dialog.getByLabel(t("tags.group"))).toHaveValue(/.+/);
  await save();
  await expect(group.locator(".tag-rows li")).toHaveText([/Тормоза/]);
  await page.getByRole("button", { name: t("tags.new"), exact: true }).click();
  await dialog.getByLabel(t("tags.name")).fill("VIP");
  await save();
  await expect(page.locator("section.card", { hasText: t("tags.no_group") }).locator(".tag-rows li")).toHaveText([/VIP/]);

  // In the card of a vehicle the group is not offered: it is for the items.
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await page.getByRole("button", { name: t("tags.change"), exact: true }).click();
  await expect(dialog.getByRole("button", { name: "VIP", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Тормоза", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // Offered everywhere once nothing is marked for the group.
  await page.goto(`${s.base}/company/tags`);
  await group.getByRole("button", { name: t("tags.group_edit_named", { group: "Категории запчастей" }), exact: true }).click();
  await dialog.getByRole("checkbox", { name: t("search.kind_item"), exact: true }).uncheck();
  await save();
  await expect(group).toContainText(t("tags.group_where", { kinds: t("tags.group_everywhere") }));
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await page.getByRole("button", { name: t("tags.change"), exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Категории запчастей", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Тормоза", exact: true }).click();
  await save();
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["Тормоза"]);

  // The group removed, its tag stays — without a group.
  await page.goto(`${s.base}/company/tags`);
  await group.getByRole("button", { name: t("tags.group_edit_named", { group: "Категории запчастей" }), exact: true }).click();
  await dialog.getByRole("button", { name: t("tags.group_remove"), exact: true }).click();
  // No group is left: the storage places are a list of the warehouse, not a group of tags
  // (customer's decision of 22.11.2026).
  await expect(page.locator("section.tag-group")).toHaveCount(0);
  await expect(page.locator(".tag-rows li")).toHaveCount(2);
});
