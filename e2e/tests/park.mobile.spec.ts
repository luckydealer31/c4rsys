import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's remark of 18.11.2026: on an iPhone the park opened with the name of a vehicle
// squeezed into a few pixels, letter under letter. On a phone a row of the park and of the
// garage is a block of its own: the vehicle across the whole width, under it the occupancy and
// the mileage with their names, then the buttons.

test("on a phone the park and the garage show a vehicle across the width of the screen", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("park-phone"), "Анна", "Петрова");
  const s = await createCompany(page);
  const made = (
    await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
      name: "SsangYong Actyon Валера",
      location_id: s.location,
      odometer_unit: "km",
      vehicle_type: "passenger_car",
      body_type: "suv",
    })
  ).vehicle as { id: string };
  await ok(page, "PUT", `/c/${s.cmp}/tariffs/${s.tariff}/vehicles/${made.id}`, {
    rates: [{ from: 1, rate: "1000" }],
    mileage: { mode: "unlimited" },
    deposit: { required: false },
  });

  for (const path of ["/rentals?tab=park", "/vehicles"]) {
    await page.goto(`${s.base}${path}`);
    const row = page.locator("table tbody tr").filter({ hasText: "SsangYong Actyon Валера" });
    await expect(row).toBeVisible();
    const link = row.getByRole("link", { name: "SsangYong Actyon Валера" });
    // The name stands in one line and nothing goes beyond the screen.
    const box = (await link.boundingBox())!;
    const line = await link.evaluate((a) => parseFloat(getComputedStyle(a).lineHeight) || 20);
    expect(box.height).toBeLessThan(line * 1.6);
    const width = await page.evaluate(() => document.documentElement.clientWidth);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    // The names of the occupancy and of the mileage stand before their values.
    const labels = await row
      .locator("td[data-label]")
      .evaluateAll((cells) => cells.map((td) => getComputedStyle(td, "::before").content.replace(/"/g, "")));
    expect(labels).toEqual([t("vehicles.occupancy"), t("vehicles.odometer")]);
  }
  await page.goto(`${s.base}/rentals?tab=park`);
  await expect(page.locator("table tbody tr").first()).toBeVisible();
});
