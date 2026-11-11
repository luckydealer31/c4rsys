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
  await page.getByRole("button", { name: t("services.cancel"), exact: true }).click();
  await expect(page.getByText(t("services.state_cancelled")).first()).toBeVisible();
});
