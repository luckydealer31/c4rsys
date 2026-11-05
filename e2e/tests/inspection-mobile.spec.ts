import { devices, expect, test } from "@playwright/test";

import { bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Audit item 41: the inspection at issue on a phone (§ 10.1, § 10.3–10.4, § 14.3.6): started from
// the rental card, readings and a photo of the first position, saved as conducted.

test.use({ ...devices["Pixel 7"], locale: "ru-RU", timezoneId: "Asia/Bangkok" });

// A 1×1 PNG: the photo of a position.
const PHOTO = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test("the inspection at issue is filled and saved on a phone", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("inspector"), "Анна", "Петрова");
  const s = await createCompany(page);
  const issued = await ok(page, "POST", `/c/${s.cmp}/rentals/issue`, {
    vehicle_card_id: s.vehicle,
    client_id: s.client,
    tariff_id: s.tariff,
    issue_location_id: s.location,
    actual_start: bangkokIso(-1, "10:00"),
    odometer: "10000",
    fuel: "8",
  });
  const rental = (issued.rental as { id: string }).id;

  // The narrow screen: the page does not scroll sideways.
  await page.goto(`${s.base}/rentals/${rental}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_active"));
  const width = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(width).toBeLessThanOrEqual(1);

  // From the rental card: the kind, the application and the rental are already chosen.
  await page.getByRole("link", { name: t("inspections.start_issue") }).click();
  await expect(page).toHaveURL(/\/inspections\/new\?/);
  await page.getByRole("button", { name: t("inspections.create") }).click();
  await expect(page).toHaveURL(/\/inspections\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("inspections.kind_issue"));

  await page.getByLabel(t("inspections.odometer", { unit: "km" })).fill("10000");
  await page.getByLabel(t("inspections.fuel")).fill("8");

  // The photo of the first position, chosen from the files of the phone.
  const position = page.locator(".position").first();
  await position.locator("input[type=file]:not([capture])").setInputFiles({
    name: "front.png",
    mimeType: "image/png",
    buffer: PHOTO,
  });
  await expect(position.locator(".position-head .badge")).toHaveText(/^1 \/ \d+$/);

  await page.getByRole("radio", { name: t("inspections.conducted_yes") }).check();
  await page.getByRole("button", { name: t("inspections.complete") }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("inspections.state_saved"));

  // The rental card lists the saved inspection.
  await page.goto(`${s.base}/rentals/${rental}`);
  const card = page.locator("section.card", {
    has: page.getByRole("heading", { name: t("inspections.of_rental"), exact: true }),
  });
  await expect(card).toContainText(t("inspections.kind_issue"));
});
