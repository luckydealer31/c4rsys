import { expect, test } from "@playwright/test";

import { bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Found on 17.11.2026 by tests on a loaded machine: while the rate of a payment in another
// currency was being loaded, the field of the amount was made anew when the rate came — what was
// being typed into it was lost and the focus went. The line is now drawn the same in every
// state. Here the rate comes late on purpose, in the middle of the typing.
test("the amount typed while the rate is being loaded is kept, and the focus stays", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("slow-rate"), "Анна", "Петрова");
  const s = await createCompany(page);
  const rental = (
    await ok(page, "POST", `/c/${s.cmp}/bookings`, {
      vehicle_card_id: s.vehicle,
      tariff_id: s.tariff,
      client_id: s.client,
      issue_location_id: s.location,
      planned_start: bangkokIso(1, "10:00"),
      planned_end: bangkokIso(2, "10:00"),
    })
  ).rental as { id: string };
  await ok(page, "POST", `/c/${s.cmp}/cash-accounts`, { name: "Bank USD", type: "cash", currency: "USD" });

  let asked = 0;
  await page.route("**/exchange-rates/suggestion**", async (route) => {
    asked += 1;
    await new Promise((done) => setTimeout(done, 1500));
    await route.continue();
  });
  await page.goto(`${s.base}/rentals/${rental.id}`);
  await page.getByRole("button", { name: t("payments.pay"), exact: true }).click();
  const payment = page.getByRole("dialog");
  const amount = payment.getByLabel(t("payments.amount", { currency: "USD" }));
  await amount.click();
  await amount.fill("");
  // Typed for about three seconds: the rate comes in the middle.
  await amount.pressSequentially("123456", { delay: 500 });
  expect(asked).toBeGreaterThan(0);
  await expect(payment.getByLabel(t("rates.rate"), { exact: true })).toBeVisible();
  await expect(amount).toHaveValue("123456");
  await expect(amount).toBeFocused();
});
