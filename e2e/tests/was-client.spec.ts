import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 23.11.2026: a vehicle added as managed that the company already keeps
// as a client one gets no second card — the earlier card is named and an employee with the
// right takes it into management; its history stays in the same card.

test("a managed vehicle kept as a client one is not doubled", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("wasclient"), "Анна", "Петрова");
  const s = await createCompany(page);
  const earlier = (
    await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
      name: "Client Forza",
      odometer_unit: "km",
      relation: "client",
      vehicle_type: "motorcycle",
      vin: "MLHNF0975S5339652",
    })
  ).vehicle as { id: string };

  await page.goto(`/c/${s.cmp}/vehicles/new`);
  const next = page.getByRole("button", { name: t("wizard.next"), exact: true });
  const skip = page.getByRole("button", { name: t("wizard.skip"), exact: true });
  await next.click();
  for (let i = 0; i < 3; i += 1) await skip.click();
  await page.getByRole("textbox", { name: /^VIN/ }).fill("mlhnf0975s5339652");
  await next.click();
  await skip.click();
  await page.getByRole("main").getByRole("textbox").first().fill("Managed Forza");
  await next.click();
  await page.getByLabel(t("vehicles.owner_short_managed"), { exact: true }).check();
  await page.getByRole("main").locator("select").first().selectOption("none");
  await next.click();
  await next.click();
  await page.getByRole("main").locator("select").first().selectOption(s.location);
  await page.getByRole("button", { name: t("vehicles.create"), exact: true }).click();

  const notice = page.getByTestId("was-client");
  await expect(notice).toContainText("Client Forza");
  await notice.getByRole("button", { name: t("vehicles.take_into_management_button"), exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/vehicles/${earlier.id}$`));
  await expect(page.getByRole("heading", { name: "Client Forza" })).toBeVisible();
  const card = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as { relation: string };
  expect(card.relation).toBe("managed");
  // The management is ended by its button in the history of the ownership: the vehicle becomes
  // a client one, the card stays.
  await page.goto(`/c/${s.cmp}/vehicles/${earlier.id}?tab=history&part=ownership`);
  await page.getByRole("main").getByLabel(t("vehicles.lifecycle_reason")).fill("Owner took it back");
  await page.getByRole("button", { name: t("vehicles.end_management_button"), exact: true }).click();
  await expect
    .poll(async () => ((await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as { relation: string }).relation)
    .toBe("client");
});
