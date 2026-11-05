import { expect, test, type Locator } from "@playwright/test";

import { bangkok, createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Audit item 41: a booking made in the interface and the issue by that booking (§ 14.1, § 14.4.1).
// The company is prepared through the API; the booking and the issue go through the screens.

async function choose(select: Locator, text: string) {
  const option = select.locator("option", { hasText: text });
  await expect(option).toHaveCount(1);
  await select.selectOption((await option.getAttribute("value")) ?? "");
}

test("a booking is issued by its own screen", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("booking"), "Анна", "Петрова");
  const s = await createCompany(page);

  // The booking: vehicle, period tomorrow 10:00 — the day after 10:00, tariff, location, client.
  await page.goto(`${s.base}/rentals/book`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(t("bookings.new"));
  await choose(page.getByRole("combobox", { name: t("rentals.vehicle"), exact: true }), "Toyota Yaris");
  await page.getByLabel(t("bookings.planned_start")).fill(bangkok(1, "10:00"));
  await page.getByLabel(t("rentals.planned_end")).fill(bangkok(2, "10:00"));
  await page.getByRole("button", { name: t("bookings.check") }).click();
  await expect(page.getByText(t("bookings.free"))).toBeVisible();
  await choose(page.getByRole("combobox", { name: t("rentals.tariff"), exact: true }), "Сутки");
  await page.getByRole("combobox", { name: t("rentals.issue_location"), exact: true }).selectOption({ label: "Пхукет" });
  await page.getByLabel(t("rentals.find_client")).fill("Иван Петров");
  await page.getByRole("button", { name: t("rentals.search") }).click();
  await page.getByRole("button", { name: /Иван Петров ·/ }).click();
  await page.getByRole("button", { name: t("bookings.create") }).click();

  // The booking card; the vehicle is held for its period.
  await expect(page).toHaveURL(/\/rentals\/[0-9a-f-]+$/);
  const bookingUrl = page.url();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_booked"));

  // The issue by the booking starts from what the booking has: vehicle, tariff, client.
  await page.getByRole("link", { name: t("bookings.issue") }).click();
  await expect(page).toHaveURL(/\/rentals\/new\?booking=/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("bookings.issue_title", { number: "" }).trim());
  await expect(page.getByRole("combobox", { name: t("rentals.vehicle"), exact: true })).toBeDisabled();
  await expect(page.getByText("Иван Петров").first()).toBeVisible();
  await page.getByLabel(t("rentals.odometer", { unit: "km" })).fill("10000");
  await page.getByLabel(t("rentals.fuel")).fill("8");
  await page.getByRole("button", { name: t("rentals.confirm_issue") }).click();

  // The same record becomes the active rental (§ 14.4.1): the booking's address opens it.
  await expect(page).toHaveURL(bookingUrl);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_active"));
});
