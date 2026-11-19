import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 17.11.2026 (§ 11.1.9): a storage place of the warehouse is no free text
// — it is a tag of the group «Storage places», which the system keeps among the tags of the
// company. The stock shows the places and is searched by them; a place is changed by choosing.
test("storage places are tags of their group", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("places"), "Анна", "Петрова");
  const s = await createCompany(page);
  await ok(page, "POST", `/c/${s.cmp}/modules/warehouse/connect`, { locations: [s.location] });
  const locations = (await ok(page, "GET", `/c/${s.cmp}/locations`)).locations as {
    contexts: { id: string; type: string }[];
  }[];
  const context = locations.flatMap((l) => l.contexts).find((c) => c.type === "warehouse")!.id;
  const item = (await ok(page, "POST", `/c/${s.cmp}/warehouse/items`, { name: "Колодки", unit: "piece" })).item as {
    id: string;
  };
  const receipt = (
    await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts`, {
      source: "purchase",
      context_id: context,
      lines: [{ item_id: item.id, quantity: 1, unit_cost: "100", tags: ["Стеллаж 1"] }],
    })
  ).receipt as { id: string; version: number };
  await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts/${receipt.id}/post`, { expected_version: receipt.version });

  // The place written in the receipt is a tag of the group of the system.
  await page.goto(`${s.base}/company/tags`);
  const group = page.locator("section.tag-group", { hasText: t("tags.system_group_storage_places") });
  await expect(group.locator(".tag-rows li")).toHaveText([/Стеллаж 1/]);
  await expect(group).toContainText(t("tags.count", { count: 1 }));

  // The stock shows the place and is searched by it.
  await page.goto(`${s.base}/warehouse`);
  const rows = page.locator("table tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("Стеллаж 1");
  await page.getByLabel(t("warehouse.search")).fill("стеллаж 1");
  await expect(rows).toHaveCount(1);
  await page.getByLabel(t("warehouse.search")).fill("стеллаж 9");
  await expect(page.getByText(t("stock.empty"))).toBeVisible();

  // The places of a unit are chosen, not written: a new one is typed by whoever may make it.
  await page.goto(`${s.base}/warehouse/items/${item.id}`);
  await page.getByRole("button", { name: t("stock.edit_tags"), exact: true }).first().click();
  const add = page.getByRole("combobox", { name: t("tags.place_add") });
  await add.fill("Стеллаж 2");
  await add.press("Enter");
  await expect(page.locator(".place-tags .tag-chip")).toHaveText([/Стеллаж 1/, /Стеллаж 2/]);
  await page.locator("tr", { has: page.locator(".place-tags") }).getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(page.getByText("Стеллаж 1, Стеллаж 2")).toBeVisible();
  await page.goto(`${s.base}/company/tags`);
  await expect(group.locator(".tag-rows li")).toHaveText([/Стеллаж 1/, /Стеллаж 2/]);

  // The section is laid out as the others are (customer's remarks of 20.11.2026): no heading
  // on the screen, the tabs at the left, the buttons of the open tab in a column at the right.
  await page.goto(`${s.base}/warehouse?tab=stock`);
  const side = page.locator(".side-actions");
  await expect(side.getByRole("link", { name: t("receipts.create") })).toBeVisible();
  await expect(side.getByRole("link", { name: t("sales.sell") })).toBeVisible();
  await expect(page.locator("main").getByRole("link", { name: t("receipts.create") })).toHaveCount(1);
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toHaveText(t("nav.warehouse"));
  expect((await heading.boundingBox())!.width).toBeLessThan(3);
  const first = (await page.getByRole("tab").first().boundingBox())!;
  expect(first.x).toBeLessThan(200);
  expect((await side.boundingBox())!.x).toBeGreaterThan(first.x + 600);
  await page.getByRole("tab", { name: t("warehouse.tab_operations"), exact: true }).click();
  await expect(side.getByRole("link", { name: t("operations.new_move") })).toBeVisible();
  await page.getByRole("tab", { name: t("warehouse.tab_items"), exact: true }).click();
  await expect(side.getByRole("link", { name: t("warehouse.create_item") })).toBeVisible();
  await page.getByRole("tab", { name: t("warehouse.tab_reserves"), exact: true }).click();
  await expect(side).toHaveCount(0);

  // The stock is filtered by a warehouse (customer's decision of 20.11.2026): with two of them
  // a list appears; on the warehouse where nothing lies the list is empty. The tab of the
  // warehouses is «Settings» now and holds the settings only.
  const second = ((await ok(page, "POST", `/c/${s.cmp}/locations`, { name: "Чалонг" })).location as { id: string }).id;
  await ok(page, "POST", `/c/${s.cmp}/locations/${second}/contexts`, { type: "warehouse" });
  await page.goto(`${s.base}/warehouse?tab=stock`);
  const where = page.getByRole("combobox", { name: t("stock.warehouse"), exact: true });
  await expect(where).toBeVisible();
  const lines = page.locator("table tbody tr");
  await expect(lines).toHaveCount(1);
  await where.selectOption({ label: "Чалонг" });
  await expect(lines).toHaveCount(0);
  await expect(page.getByText(t("stock.empty"))).toBeVisible();
  await where.selectOption({ label: "Пхукет" });
  await expect(lines).toHaveCount(1);
  await expect(page.getByRole("columnheader", { name: t("stock.by_warehouse") })).toHaveCount(0);
  await page.getByRole("tab", { name: t("warehouse.tab_storages"), exact: true }).click();
  await expect(page.getByRole("heading", { name: t("operations.settings_title") })).toBeVisible();
  await expect(page.locator("section.card")).toHaveCount(1);
});
