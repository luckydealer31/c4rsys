import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 11.11.2026: a service made by mistake is cancelled in its order also
// after it was started and completed, and the order can be cancelled then. The company and the
// completed service are prepared through the API; the cancellation goes through the screen.
test("a completed service is cancelled in its order", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("service-cancel"), "Анна", "Петрова");
  const s = await createCompany(page);
  const service = (
    (await ok(page, "POST", `/c/${s.cmp}/extra-services`, {
      name: "Мойка",
      pricing: "once",
      price: "300",
      currency: "THB",
      locations: "all",
      vehicles: [],
      tariffs: [],
    })).service as { id: string }
  ).id;
  let line = (
    await ok(page, "POST", `/c/${s.cmp}/service-lines`, {
      service_id: service,
      location_id: s.location,
      client_id: s.client,
    })
  ).line as { id: string; version: number; order_id: string };
  for (const action of ["start", "complete"]) {
    line = (
      await ok(page, "POST", `/c/${s.cmp}/service-lines/${line.id}/${action}`, { expected_version: line.version })
    ).line as typeof line;
  }

  await page.goto(`${s.base}/orders/${line.order_id}`);
  await page.getByRole("button", { name: t("services.settings"), exact: true }).click();
  await page.getByLabel(t("charges.cancel_reason"), { exact: true }).first().fill("по ошибке");
  // The service is the only position of its order: the order is offered to be cancelled with
  // it (decision of 16.11.2026), and is.
  await expect(page.getByRole("switch", { name: /№/ })).toBeChecked();
  await page.getByRole("button", { name: t("services.cancel"), exact: true }).click();
  await expect(page.getByText(t("services.state_cancelled")).first()).toBeVisible();
  await expect(page.getByText(t("orders.state_cancelled")).first()).toBeVisible();

  // The ordered services are filtered as the rentals are (decision of 17.11.2026): a cancelled
  // one is not listed until «Cancelled» is switched on; it was started, so it is a service,
  // not a booking.
  await page.goto(`${s.base}/rentals?tab=services`);
  const rows = page.locator("table tbody tr");
  await expect(page.getByText(t("rentals.none_found"))).toBeVisible();
  await page.getByRole("switch", { name: t("rentals.filter_cancelled") }).check();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("№ 1");
  await page.getByRole("button", { name: t("rentals.filter_bookings"), exact: true }).click();
  await expect(rows).toHaveCount(0);
  await page.getByRole("button", { name: t("rentals.filter_services"), exact: true }).click();
  await expect(rows).toHaveCount(1);

  // Customer's decisions of 22.11.2026: goods leave the warehouse into a service that already
  // exists; without a work they go into the order of the service, and there they are linked to
  // a work and unlinked again — the same line of the order with the same price. The same
  // company serves: one more registration would pass the hourly limit of the server.
  await ok(page, "POST", `/c/${s.cmp}/modules/warehouse/connect`, { locations: [s.location] });
  await ok(page, "POST", `/c/${s.cmp}/modules/service/connect`, { locations: [s.location] });
  const locations = (await ok(page, "GET", `/c/${s.cmp}/locations`)).locations as {
    contexts: { id: string; type: string }[];
  }[];
  const context = locations.flatMap((l) => l.contexts).find((c) => c.type === "warehouse")!.id;
  const item = ((await ok(page, "POST", `/c/${s.cmp}/warehouse/items`, { name: "Фильтр", unit: "piece" })).item as { id: string }).id;
  const receipt = (
    await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts`, {
      source: "purchase",
      context_id: context,
      lines: [{ item_id: item, quantity: 1, unit_cost: "200", sale_price: "350" }],
    })
  ).receipt as { id: string; version: number };
  await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts/${receipt.id}/post`, { expected_version: receipt.version });
  const work = ((await ok(page, "POST", `/c/${s.cmp}/service/works`, { name: "Замена фильтра", pricing: "fixed", price: "500" })).work as { id: string }).id;
  const operation = (
    await ok(page, "POST", `/c/${s.cmp}/service/operations`, {
      vehicle_card_id: s.vehicle,
      location_id: s.location,
      reason: "Плановое обслуживание",
      client_id: s.client,
      work_ids: [work],
    })
  ).operation as { id: string };

  await page.goto(`${s.base}/warehouse/stock/${item}`);
  await page.getByRole("checkbox", { name: t("stock.mark_all"), exact: true }).check();
  await page.locator(".units-bar").getByRole("button", { name: t("send.to_service"), exact: true }).click();
  const toService = page.getByRole("dialog", { name: t("send.to_service") });
  await toService.locator(".pick-list").getByRole("button").first().click();
  await expect(toService.getByRole("button", { name: "Замена фильтра", exact: true })).toBeVisible();
  await toService.getByRole("button", { name: t("send.without_work"), exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/service/operations/${operation.id}$`));

  const free = page.locator(".free-goods");
  await expect(free).toContainText("Фильтр");
  await expect(free).toContainText("350.00");
  await free.getByRole("combobox", { name: t("goods.work") }).selectOption({ label: "Замена фильтра" });
  await free.getByRole("button", { name: t("goods.link"), exact: true }).click();
  const linked = page.locator(".job-goods");
  await expect(linked).toContainText("Фильтр");
  await expect(linked).toContainText("350.00");
  await expect(free).toHaveCount(0);
  await linked.getByRole("button", { name: t("goods.unlink"), exact: true }).click();
  await expect(page.locator(".free-goods")).toContainText("Фильтр");
  await expect(linked).toHaveCount(0);
});
