import { expect, test } from "@playwright/test";

import { bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Decision of 16.11.2026: an order charged in several currencies is settled currency by
// currency. The deposit set off against such an order names the currency as a payment does; in
// another currency the amount, the rate and the equivalent stand in one line. The booking, the
// service in USD and the received deposit are prepared through the API.
test("the deposit is set off against one currency of an order charged in two", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("deposit-offset"), "Анна", "Петрова");
  const s = await createCompany(page);
  await ok(page, "PUT", `/c/${s.cmp}/tariffs/${s.tariff}/vehicles/${s.vehicle}`, {
    rates: [{ from: 1, rate: "1000" }],
    mileage: { mode: "unlimited" },
    deposit: { required: true, amount: "5000" },
  });
  const service = (
    (await ok(page, "POST", `/c/${s.cmp}/extra-services`, {
      name: "Телефон",
      pricing: "once",
      price: "10",
      currency: "USD",
      locations: "all",
      vehicles: [],
      tariffs: [],
    })).service as { id: string }
  ).id;
  const rental = (
    await ok(page, "POST", `/c/${s.cmp}/bookings`, {
      vehicle_card_id: s.vehicle,
      tariff_id: s.tariff,
      client_id: s.client,
      issue_location_id: s.location,
      planned_start: bangkokIso(1, "10:00"),
      planned_end: bangkokIso(2, "10:00"),
    })
  ).rental as { id: string; version: number };
  await ok(page, "POST", `/c/${s.cmp}/rentals/${rental.id}/services`, {
    service_id: service,
    expected_version: rental.version,
  });
  const account = (
    (await ok(page, "POST", `/c/${s.cmp}/cash-accounts`, { name: "Касса", type: "cash", currency: "THB" }))
      .cash_account as { id: string }
  ).id;
  await ok(page, "POST", `/c/${s.cmp}/payments/cash`, {
    cash_account_id: account,
    client_id: s.client,
    amount: "5000",
    currency: "THB",
    allocations: [],
    deposits: [{ rental_id: rental.id, amount: "5000" }],
  });

  await page.goto(`${s.base}/rentals/${rental.id}`);
  const card = page.locator("section.card", { has: page.getByRole("heading", { name: t("deposit.title"), exact: true }) });
  const used = (offset: string, refunded = "0.00") =>
    t("deposit.used_value", { offset, refunded, to_balance: "0.00", currency: "THB" });
  await card.getByRole("button", { name: t("deposit.action_offset"), exact: true }).click();
  // The currency of the charges is chosen: the deposit's own first, so no rate is asked.
  const currency = card.getByLabel(t("payments.settle_currency"));
  await expect(currency).toHaveValue("THB");
  await expect(card.getByLabel(t("rates.rate"), { exact: true })).toHaveCount(0);
  const amount = card.getByLabel(t("payments.amount", { currency: "THB" }));
  await amount.fill("400");
  await card.getByRole("button", { name: t("deposit.action_offset"), exact: true }).last().click();
  await expect(card).toContainText(used("400.00"));

  // Against the charges in USD: the rate and the equivalent follow each other.
  await card.getByRole("button", { name: t("deposit.action_offset"), exact: true }).click();
  await card.getByLabel(t("payments.settle_currency")).selectOption("USD");
  await card.getByLabel(t("payments.amount", { currency: "THB" })).fill("370");
  const rate = card.getByLabel(t("rates.rate"), { exact: true });
  const equivalent = card.getByLabel(t("rates.equivalent", { currency: "USD" }), { exact: true });
  // The rate is shown the way it is said: 1 USD = 37 THB, not 1 THB = 0.027 USD.
  await equivalent.fill("10");
  await expect(rate).toHaveValue("37");
  await rate.fill("36.5");
  await expect(equivalent).toHaveValue("10.14");
  await card.getByLabel(t("payments.amount", { currency: "THB" })).fill("365");
  await expect(rate).toHaveValue("36.5");
  await expect(equivalent).toHaveValue("10.00");
  await card.getByRole("button", { name: t("deposit.action_offset"), exact: true }).last().click();
  await expect(card).toContainText(used("765.00"));

  // A part of the deposit comes in USD by 37; paid back in USD it gives what came, and no
  // rate is asked. Above that part the rate of the payout is needed.
  const usd = (
    (await ok(page, "POST", `/c/${s.cmp}/cash-accounts`, { name: "Bank USD", type: "cash", currency: "USD" }))
      .cash_account as { id: string }
  ).id;
  await ok(page, "POST", `/c/${s.cmp}/payments/cash`, {
    cash_account_id: usd,
    client_id: s.client,
    amount: "100",
    currency: "USD",
    allocations: [],
    deposits: [{ rental_id: rental.id, amount: "100", rate: "37" }],
  });
  await page.reload();
  await card.getByRole("button", { name: t("deposit.action_refund"), exact: true }).click();
  // No account stands there by itself: until one is chosen the refund cannot be made.
  await expect(card.getByLabel(t("money.account"))).toHaveValue("");
  await expect(card.getByRole("button", { name: t("deposit.action_refund"), exact: true }).last()).toBeDisabled();
  await card.getByLabel(t("money.account")).selectOption({ label: "Bank USD · USD" });
  await expect(card.getByLabel(t("rates.rate"), { exact: true })).toHaveCount(1);
  await card.getByLabel(t("payments.amount", { currency: "THB" })).fill("3700");
  await expect(card.getByLabel(t("rates.rate"), { exact: true })).toHaveCount(0);
  const payout = card.getByLabel(t("rates.equivalent", { currency: "USD" }), { exact: true });
  await expect(payout).toHaveValue("100.00");
  await expect(payout).toBeDisabled();
  await card.getByRole("button", { name: t("deposit.action_refund"), exact: true }).last().click();
  await expect(card).toContainText(used("765.00", "3700.00"));
  await expect(card).toContainText("3700.00 THB (100.0000 USD)");
});
