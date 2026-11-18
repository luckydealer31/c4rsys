import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 18.11.2026: the archived tariffs and services are not listed in
// «Tariffs and services» until they are switched on — with time there are many of them.
test("archived tariffs are listed only when switched on", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("catalog"), "Анна", "Петрова");
  const s = await createCompany(page);
  await page.goto(`${s.base}/rentals?tab=catalog`);
  const tariffs = page.locator("section.card", { has: page.getByRole("heading", { name: t("settings.tariffs"), exact: true }) });
  await expect(tariffs.locator("a.list-link")).toHaveText([/Сутки/]);
  // Nothing is archived: there is nothing to switch on.
  await expect(page.getByRole("switch", { name: t("rentals.catalog_archived") })).toHaveCount(0);

  const old = (await ok(page, "POST", `/c/${s.cmp}/tariffs`, { name: "Старый", ranges: [{ from: 1, to: null }] }))
    .tariff as { id: string; version: number };
  await ok(page, "POST", `/c/${s.cmp}/tariffs/${old.id}/archive`, { expected_version: old.version });
  await page.reload();
  await expect(tariffs.locator("a.list-link")).toHaveText([/Сутки/]);
  const archived = page.getByRole("switch", { name: t("rentals.catalog_archived") });
  await expect(archived).not.toBeChecked();
  await archived.check();
  await expect(tariffs.locator("a.list-link")).toHaveCount(2);
  await expect(tariffs.locator("a.list-link", { hasText: "Старый" })).toContainText(t("tariffs.archived"));
  await archived.uncheck();
  await expect(tariffs.locator("a.list-link")).toHaveCount(1);
});
