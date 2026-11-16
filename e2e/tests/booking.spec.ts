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
  // The period in units of the tariff: one day by the dates; three units move the end.
  const units = page.getByLabel(t("bookings.units", { unit: t("tariffs.short_day") }));
  await expect(units).toHaveValue("1");
  await units.fill("3");
  await expect(page.getByLabel(t("rentals.planned_end"))).toHaveValue(bangkok(4, "10:00"));
  await page.getByLabel(t("rentals.planned_end")).fill(bangkok(2, "10:00"));
  await expect(units).toHaveValue("1");
  await page.getByRole("combobox", { name: t("rentals.issue_location"), exact: true }).selectOption({ label: "Пхукет" });
  // Without a client the contact is written by hand; back to the choice of a client.
  await page.getByRole("button", { name: t("bookings.no_client"), exact: true }).click();
  await expect(page.getByLabel(t("bookings.no_client_contact"))).toBeVisible();
  await page.getByRole("button", { name: t("bookings.pick_client") }).click();
  // «Create a client» is offered at once, before anything is typed.
  await page.getByRole("combobox", { name: t("rentals.find_client") }).click();
  await expect(page.getByRole("button", { name: `+ ${t("rentals.new_client")}` })).toBeVisible();
  // The window of a new client: one title, the button says «Create».
  await page.getByRole("button", { name: `+ ${t("rentals.new_client")}` }).click();
  const window = page.getByRole("dialog");
  await expect(window.getByRole("heading", { name: t("clients.create_title") })).toHaveCount(1);
  await expect(window.getByRole("button", { name: t("common.create"), exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("combobox", { name: t("rentals.find_client") }).fill("Иван");
  await expect(page.getByRole("button", { name: /Иван Петров ·/ })).toBeVisible();
  await page.getByRole("button", { name: /Иван Петров ·/ }).click();
  await page.getByRole("button", { name: t("bookings.create") }).click();

  // The booking card; the vehicle is held for its period.
  await expect(page).toHaveURL(/\/rentals\/[0-9a-f-]+$/);
  const bookingUrl = page.url();
  // The card of a booking: one block «Booking» with the order by its number; no actual start
  // and no inspections — those belong to a rental.
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("bookings.title", { number: 1 }));
  const block = page.locator("section.card", { has: page.getByRole("heading", { name: t("bookings.block"), exact: true }) });
  await expect(block.getByRole("link", { name: t("bookings.order_number", { number: 1 }) })).toBeVisible();
  await expect(block).not.toContainText(t("rentals.actual_start"));
  await expect(page.getByRole("heading", { name: t("inspections.of_rental") })).toHaveCount(0);
  await expect(page.locator("section.card", { hasText: t("bookings.total") })).toContainText("1000.00 THB");
  // A service is added by the button «Add», which opens the choice; no field stands open.
  const services = page.locator("section.card", { has: page.getByRole("heading", { name: t("services.title"), exact: true }) });
  await expect(services.getByLabel(t("services.service"))).toHaveCount(0);
  await services.getByRole("button", { name: t("common.add"), exact: true }).click();
  await services.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  await expect(services.getByRole("button", { name: t("common.add"), exact: true })).toBeVisible();

  // A booking is not issued by itself: it is turned into a rental, which starts from what the
  // booking has — vehicle, tariff, client. No readings are asked at that moment.
  await page.getByRole("link", { name: t("bookings.to_rental") }).click();
  await expect(page).toHaveURL(/\/rentals\/new\?booking=/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("bookings.to_rental_title", { number: "" }).split(":")[0].trim());
  await expect(page.getByRole("combobox", { name: t("rentals.vehicle"), exact: true })).toBeDisabled();
  await expect(page.getByText("Иван Петров").first()).toBeVisible();
  await expect(page.getByLabel(t("rentals.odometer", { unit: "km" }))).toHaveCount(0);
  await expect(page.getByRole("button", { name: t("rentals.confirm_issue") })).toHaveCount(0);
  // «Without an inspection» is said here; then the rental is issued with its readings.
  await page.getByLabel(t("rentals.without_inspection")).check();
  // The cost is counted by itself from the period of the booking.
  await expect(page.locator("section.card", { hasText: t("rentals.preliminary") }).last()).toContainText("× 1000 THB = ");
  await page.getByRole("button", { name: t("bookings.to_rental") }).click();
  await expect(page).toHaveURL(bookingUrl);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_awaiting"));
  await page.getByRole("link", { name: t("rentals.issue"), exact: true }).click();
  await page.getByLabel(t("rentals.odometer", { unit: "km" })).fill("10000");
  await page.getByLabel(t("rentals.fuel")).fill("8");
  await page.getByRole("button", { name: t("rentals.confirm_issue") }).click();

  // The same record becomes the active rental (§ 14.4.1): the booking's address opens it.
  await expect(page).toHaveURL(bookingUrl);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_active"));
});
