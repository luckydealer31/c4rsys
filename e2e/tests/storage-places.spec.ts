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
});
