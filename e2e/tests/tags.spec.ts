import { expect, test, type Page } from "@playwright/test";

import { bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decisions of 17.11.2026. Tags of the company — made with the right, with a colour —
// are put on records of any section and find them together on the page of the tag; a personal
// tag is made by any employee for themself and is drawn in another shape.

async function makeTag(page: Page, button: string, name: string, color: string) {
  await page.getByRole("button", { name: button, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(t("tags.name")).fill(name);
  await dialog.getByRole("radio", { name: t(`tags.color_${color}`), exact: true }).click();
  await dialog.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/** Opens the window of the tags of the record shown and presses the given tags there. */
async function choose(page: Page, names: string[]) {
  await page.getByRole("button", { name: t("tags.change"), exact: true }).click();
  const dialog = page.getByRole("dialog");
  for (const name of names) await dialog.getByRole("button", { name, exact: true }).click();
  await dialog.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

test("tags of the company and personal tags are put on records and find them together", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tags"), "Анна", "Петрова");
  const s = await createCompany(page);

  // The tab «Tags» of the company: a tag of the company and a personal one.
  await page.goto(`${s.base}/company`);
  await page.getByRole("tab", { name: t("company.tab_tags"), exact: true }).click();
  await expect(page).toHaveURL(/\/company\/tags$/);
  await expect(page.getByText(t("tags.none"), { exact: true })).toBeVisible();
  await makeTag(page, t("tags.new"), "VIP", "red");
  await makeTag(page, t("tags.new_personal"), "Проверить", "green");
  const rows = page.locator(".tag-rows li");
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "VIP" })).toContainText(t("tags.count", { count: 0 }));
  // The two are drawn differently.
  await expect(rows.filter({ hasText: "VIP" }).locator(".tag-chip")).not.toHaveClass(/personal/);
  await expect(rows.filter({ hasText: "Проверить" }).locator(".tag-chip")).toHaveClass(/personal/);

  // Put on a client and on a vehicle from their cards.
  await page.goto(`${s.base}/clients/${s.client}`);
  await choose(page, ["VIP"]);
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["VIP"]);
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await choose(page, ["VIP", "Проверить"]);
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["VIP", "Проверить"]);

  // The tag leads to everything it is put on, by sections.
  await page.locator(".tags-line").getByRole("link", { name: "VIP", exact: true }).click();
  await expect(page).toHaveURL(/\/company\/tags\/[0-9a-f-]+$/);
  const group = (kind: string) => page.locator("section.card", { has: page.getByRole("heading", { name: t(`search.kind_${kind}`), exact: true }) });
  await expect(group("client").getByRole("link")).toHaveCount(1);
  await expect(group("vehicle").getByRole("link", { name: "Toyota Yaris" })).toBeVisible();

  // The search inside a section finds by the tag too: in the garage — the vehicle it is put on.
  await page.goto(`${s.base}/vehicles`);
  const box = page.getByRole("searchbox", { name: t("search.section_placeholder") });
  await box.fill("VIP");
  await box.press("Enter");
  const found = page.locator("table tbody tr");
  await expect(found).toHaveCount(1);
  await expect(found).toContainText("Toyota Yaris");
  await box.fill("нет такого тега");
  await box.press("Enter");
  await expect(found).toHaveCount(0);

  // The list of rentals and bookings is searched too — by what a row shows and by a tag.
  const booking = (
    await ok(page, "POST", `/c/${s.cmp}/bookings`, {
      vehicle_card_id: s.vehicle,
      tariff_id: s.tariff,
      client_id: s.client,
      issue_location_id: s.location,
      planned_start: bangkokIso(1, "10:00"),
      planned_end: bangkokIso(2, "10:00"),
    })
  ).rental as { id: string };
  await page.goto(`${s.base}/rentals/${booking.id}`);
  await choose(page, ["VIP"]);
  await page.goto(`${s.base}/rentals?tab=rentals`);
  const rentals = page.locator("table tbody tr");
  await expect(rentals).toHaveCount(1);
  const search = page.getByRole("searchbox", { name: t("search.section_placeholder") });
  for (const [text, count] of [["VIP", 1], ["yaris", 1], ["Петров", 1], ["нет такого", 0]] as const) {
    await search.fill(text);
    await search.press("Enter");
    await expect(rentals).toHaveCount(count);
  }

  // It is found in the single search line, and renamed everywhere at once.
  await page.goto(`${s.base}/search?q=VIP`);
  await group("tag").getByRole("link", { name: "VIP", exact: true }).click();
  await page.getByRole("button", { name: t("tags.edit"), exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(t("tags.name")).fill("Gold");
  await dialog.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".tag-title")).toHaveText("Gold");
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["Gold", "Проверить"]);

  // Archived, it is shown on no record; restored, its marks are back.
  await page.goto(`${s.base}/company/tags`);
  // The client, the vehicle and the booking.
  await expect(rows.filter({ hasText: "Gold" })).toContainText(t("tags.count", { count: 3 }));
  await rows.filter({ hasText: "Gold" }).getByRole("button", { name: t("tags.archive"), exact: true }).click();
  await expect(rows).toHaveCount(1);
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["Проверить"]);
  await page.goto(`${s.base}/company/tags`);
  await page.getByRole("switch", { name: t("tags.show_archived") }).check();
  await rows.filter({ hasText: "Gold" }).getByRole("button", { name: t("tags.restore"), exact: true }).click();
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  await expect(page.locator(".tags-line .tag-chip")).toHaveText(["Gold", "Проверить"]);
});
