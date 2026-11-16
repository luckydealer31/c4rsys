import { expect, test, type Page } from "@playwright/test";

import { bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer, type Company } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 16.11.2026: when the only position of an order is cancelled, the order
// is offered to be cancelled with it, so an empty order does not stay forgotten. The offer is a
// switch, on by itself; switched off, the order stays open. Bookings are prepared through the
// API, the cancellation goes through the screen.
async function book(page: Page, s: Company, day: number) {
  return (
    await ok(page, "POST", `/c/${s.cmp}/bookings`, {
      vehicle_card_id: s.vehicle,
      tariff_id: s.tariff,
      client_id: s.client,
      issue_location_id: s.location,
      planned_start: bangkokIso(day, "10:00"),
      planned_end: bangkokIso(day + 1, "10:00"),
    })
  ).rental as { id: string; order_id: string };
}

async function cancel(page: Page, s: Company, rental: { id: string }, withOrder: boolean, number: number) {
  await page.goto(`${s.base}/rentals/${rental.id}`);
  await page.getByRole("button", { name: t("bookings.action_cancel"), exact: true }).click();
  const form = page.locator("form", { has: page.getByLabel(t("bookings.cancel_reason")) });
  await form.getByLabel(t("bookings.cancel_reason")).fill("клиент передумал");
  const offer = form.getByRole("switch", { name: t("orders.cancel_with", { number }) });
  await expect(offer).toBeChecked();
  if (!withOrder) await offer.uncheck();
  await form.getByRole("button", { name: t("bookings.cancel"), exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_cancelled"));
}

test("an order is cancelled with its only booking, or stays when the offer is switched off", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("cancel-with-order"), "Анна", "Петрова");
  const s = await createCompany(page);
  const first = await book(page, s, 1);
  const second = await book(page, s, 3);

  await cancel(page, s, first, true, 1);
  await page.goto(`${s.base}/orders/${first.order_id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("orders.state_cancelled"));

  await cancel(page, s, second, false, 2);
  await page.goto(`${s.base}/orders/${second.order_id}`);
  await expect(page.getByRole("heading", { level: 1 })).not.toContainText(t("orders.state_cancelled"));

  // The list of rentals and bookings: a third booking stays live. Cancelled ones are not
  // listed until they are switched on; the newest stands first, by the number of the order.
  await book(page, s, 5);
  await page.goto(`${s.base}/rentals`);
  const rows = page.locator("table tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("№ 3");
  await page.getByRole("switch", { name: t("rentals.filter_cancelled") }).check();
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("№ 3");
  await expect(rows.last()).toContainText("№ 1");
  // A click on the heading of the number turns the order over.
  await page.getByRole("button", { name: new RegExp(`^${t("orders.order")}`) }).click();
  await expect(rows.first()).toContainText("№ 1");
  // Rentals alone: the three are bookings, nothing is left.
  await page.getByRole("button", { name: t("rentals.filter_rentals"), exact: true }).click();
  await expect(rows).toHaveCount(0);
  await expect(page.getByText(t("rentals.none_found"))).toBeVisible();

  // The order left without positions is found among the orders and cancelled at once.
  await page.goto(`${s.base}/orders`);
  await expect(rows).toHaveCount(3);
  await page.getByRole("switch", { name: t("orders.filter_empty") }).check();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("№ 2");
  await page.getByLabel(t("orders.cancel_reason")).fill("нет позиций");
  await page.getByRole("button", { name: t("orders.cancel_empty", { count: 1 }), exact: true }).click();
  await expect(page.getByText(t("orders.none_empty"))).toBeVisible();
  await page.getByRole("switch", { name: t("orders.filter_empty") }).uncheck();
  await expect(rows.filter({ hasText: t("orders.state_cancelled") })).toHaveCount(2);
});
