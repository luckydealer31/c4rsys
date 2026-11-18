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

  // The wizard of a tariff asks what it is counted by: the minute, the hour, the day, the month;
  // the two ways of counting a day stand under «by the day» (customer's remark of 18.11.2026).
  await page.goto(`${s.base}/rentals/tariffs/new`);
  const next = page.getByRole("button", { name: t("wizard.next"), exact: true });
  await page.getByRole("textbox").last().fill("Месячный");
  await next.click();
  await page.getByRole("combobox").last().selectOption("THB");
  await next.click();
  await expect(page.getByRole("heading", { name: t("tariffs.billing"), exact: true })).toBeVisible();
  const kinds = ["minute", "hour", "day", "month"].map((k) => t(`tariffs.billing_${k}`));
  await expect(page.locator("input[name=billing]")).toHaveCount(4);
  for (const kind of kinds) await expect(page.getByRole("radio", { name: kind, exact: true })).toBeVisible();
  // By the day: 24 hours from the issue by itself; a set time is asked only for the other way.
  await page.getByRole("radio", { name: t("tariffs.billing_day"), exact: true }).check();
  await expect(page.getByRole("radio", { name: t("tariffs.day_from_issue"), exact: true })).toBeChecked();
  await expect(page.getByLabel(t("tariffs.day_boundary"))).toHaveCount(0);
  await page.getByRole("radio", { name: t("tariffs.day_fixed_boundary"), exact: true }).check();
  await expect(page.getByLabel(t("tariffs.day_boundary"))).toHaveValue("12:00");
  // The explanation of the boundary stands under «i», not printed all the time.
  await expect(page.getByText(t("tariffs.day_boundary_hint"))).toHaveCount(0);
  await page.locator(".sub-choice").getByRole("note").hover();
  await expect(page.locator(".info-pop")).toHaveText(t("tariffs.day_boundary_hint"));
  await page.mouse.move(0, 0);
  await page.getByRole("radio", { name: t("tariffs.billing_month"), exact: true }).check();
  await expect(page.getByRole("radio", { name: t("tariffs.day_from_issue"), exact: true })).toHaveCount(0);
});
