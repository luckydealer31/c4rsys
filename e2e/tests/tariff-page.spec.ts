import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decisions of 19.11.2026. On the page of a tariff the price ranges are the columns of
// the table of vehicles: the end of a range is typed in the header of its column, the end typed
// for the last column adds a new one with the same prices, × takes a column away. Everything is
// saved with one button.

test("the ranges of a tariff are edited in the columns of its price table", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tariff-page"), "Анна", "Петрова");
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
    mileage: { mode: "unlimited" },
    deposit: { required: false },
  });
  const rates = async () =>
    (
      (await ok(page, "GET", `/c/${s.cmp}/tariffs/${tariff}/vehicles`)) as {
        vehicles: { vehicle_card_id: string; rates: { from: number; rate: string }[] }[];
      }
    ).vehicles
      .find((v) => v.vehicle_card_id === s.vehicle)!
      .rates.map((r) => [r.from, Number(r.rate)]);
  const ranges = async () =>
    (
      (await ok(page, "GET", `/c/${s.cmp}/tariffs/${tariff}`)) as {
        tariff: { ranges: { from: number; to: number | null }[] };
      }
    ).tariff.ranges.map((r) => [r.from, r.to]);

  await page.goto(`${s.base}/rentals/tariffs/${tariff}`);
  // No separate block of ranges any more.
  await expect(page.getByRole("heading", { name: t("tariffs.ranges"), exact: true })).toHaveCount(0);
  const end = (from: number) => page.getByRole("textbox", { name: t("terms.range_to", { from }), exact: true });
  const save = page.getByRole("button", { name: t("common.save"), exact: true });
  await expect(end(1)).toHaveValue("60");
  await expect(save).toBeDisabled();

  // The first range ends at 90: the second begins with 91 and keeps its price.
  await end(1).fill("90");
  await expect(end(91)).toBeVisible();
  const row = page.locator("table.prices-table tbody tr", { hasText: "Toyota Yaris" });
  const price = (n: number) => row.locator("td").nth(n).locator("input");
  await expect(price(1)).toHaveValue("20");
  await expect(price(2)).toHaveValue("18");
  // The end typed for the last range adds a new one with the same prices.
  await end(91).fill("600");
  await expect(end(601)).toBeVisible();
  await expect(price(3)).toHaveValue("18");
  await price(3).fill("15");
  // The header of a range stands in one line: its beginning, the field of its end and × level.
  const head = page.locator("th.range-head").nth(1);
  const top = async (selector: string) => (await head.locator(selector).boundingBox())!;
  const [begin, field, cross] = [await top(".range-begin"), await top(".unit-field"), await top("button")];
  for (const box of [field, cross]) expect(Math.abs(box.y + box.height / 2 - (begin.y + begin.height / 2))).toBeLessThan(4);
  // The fields of the ends are of one width, whatever the beginning of the range.
  const widths = await page.locator("th.range-head .unit-field").evaluateAll((all) => all.map((e) => Math.round(e.getBoundingClientRect().width)));
  expect(new Set(widths).size, widths.join(",")).toBe(1);
  await save.click();
  await expect(page.getByText(t("common.saved"))).toBeVisible();
  await expect.poll(ranges).toEqual([
    [1, 90],
    [91, 600],
    [601, null],
  ]);
  await expect.poll(rates).toEqual([
    [1, 20],
    [91, 18],
    [601, 15],
  ]);

  // × takes the middle range away: the next one takes its units and keeps its price.
  await page.getByRole("button", { name: t("terms.range_remove", { from: 91 }), exact: true }).click();
  await expect(end(601)).toHaveCount(0);
  await expect(price(2)).toHaveValue("15");
  await save.click();
  await expect.poll(ranges).toEqual([
    [1, 90],
    [91, null],
  ]);
  await expect.poll(rates).toEqual([
    [1, 20],
    [91, 15],
  ]);
});
