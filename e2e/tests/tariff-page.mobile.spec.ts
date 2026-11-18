import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 20.11.2026. On a phone the page of a tariff has no table: the ranges are
// a list of their own, every vehicle is a block — closed, it shows its prices in one line; opened,
// a price of each range in a line, then the mileage and the deposit.

test("on a phone a tariff shows its ranges as a list and its vehicles as blocks", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tariff-phone"), "Анна", "Петрова");
  const s = await createCompany(page);
  const tariff = (
    (await ok(page, "POST", `/c/${s.cmp}/tariffs`, {
      name: "Минуты",
      unit: "minute",
      ranges: [{ from: 1, to: 60 }, { from: 61 }],
    })) as { tariff: { id: string } }
  ).tariff.id;
  await ok(page, "PUT", `/c/${s.cmp}/tariffs/${tariff}/vehicles/${s.vehicle}`, {
    rates: [
      { from: 1, rate: "20" },
      { from: 61, rate: "18" },
    ],
    mileage: { mode: "included", included_per_unit: "3", overage_price: "15" },
    deposit: { required: true, amount: "5000" },
  });
  await ok(page, "POST", `/c/${s.cmp}/vehicles`, { name: "Honda Civic", location_id: s.location, odometer_unit: "km" });
  const rates = async () =>
    (
      (await ok(page, "GET", `/c/${s.cmp}/tariffs/${tariff}/vehicles`)) as {
        vehicles: { vehicle_card_id: string; rates: { from: number; rate: string }[] }[];
      }
    ).vehicles
      .find((v) => v.vehicle_card_id === s.vehicle)!
      .rates.map((r) => [r.from, Number(r.rate)]);

  await page.goto(`${s.base}/rentals/tariffs/${tariff}`);
  const end = (from: number) => page.getByRole("textbox", { name: t("terms.range_to", { from }), exact: true });
  await expect(end(1)).toHaveValue("60");
  await expect(page.locator("table.prices-table")).toHaveCount(0);
  // Nothing goes beyond the screen.
  const width = await page.evaluate(() => document.documentElement.clientWidth);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);

  // A closed block shows the prices in one line; a vehicle without the tariff says so.
  const yaris = page.getByRole("region", { name: "Toyota Yaris" });
  await expect(yaris).toContainText("20 · 18 THB");
  await expect(yaris).toContainText(`${t("terms.deposit")} 5000`);
  await expect(page.getByRole("region", { name: "Honda Civic" })).toContainText(t("terms.not_marked"));

  // The first range ends at 90, the end typed for the last one adds a range.
  await end(1).fill("90");
  await end(91).fill("600");
  await expect(end(601)).toBeVisible();
  // Opened, the block has a price of each range.
  await yaris.getByRole("button", { name: `Toyota Yaris: ${t("terms.prices_toggle")}` }).click();
  const price = (from: number) => yaris.getByRole("textbox", { name: `Toyota Yaris: ${t("terms.price")} ${from}`, exact: true });
  await expect(price(1)).toHaveValue("20");
  await expect(price(91)).toHaveValue("18");
  await expect(price(601)).toHaveValue("18");
  await price(601).fill("15");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect.poll(rates).toEqual([
    [1, 20],
    [91, 18],
    [601, 15],
  ]);
});
