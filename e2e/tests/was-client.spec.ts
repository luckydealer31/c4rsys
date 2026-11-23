import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { mailCode } from "../lib/docker";
import { t } from "../lib/i18n";

// Customer's decision of 23.11.2026: a vehicle added as managed that the company already keeps
// as a client one gets no second card — the earlier card is named and an employee with the
// right takes it into management; its history stays in the same card.

test("a managed vehicle kept as a client one is not doubled", async ({ page }) => {
  await waitForServer(page);
  const email = uniqueEmail("wasclient");
  await registerUser(page, email, "Анна", "Петрова");
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
  // Every action is a small button that asks what it needs in its own window (customer's remark
  // of 23.11.2026).
  await expect(page.getByRole("main").getByLabel(t("vehicles.lifecycle_reason"))).toHaveCount(0);
  await page.getByRole("button", { name: t("vehicles.end_management_button"), exact: true }).click();
  const asking = page.getByRole("dialog");
  await asking.getByLabel(t("vehicles.lifecycle_reason")).fill("Owner took it back");
  await asking.getByRole("button", { name: t("vehicles.end_management_button"), exact: true }).click();
  await expect
    .poll(async () => ((await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as { relation: string }).relation)
    .toBe("client");
  // A client card: «Take into management» asks the location and the reason; «Delete» says the
  // card is moved to the archive and then asks the reason.
  await page.getByRole("button", { name: t("vehicles.take_into_management_button"), exact: true }).click();
  await expect(asking.getByLabel(t("vehicles.location"))).toBeVisible();
  await expect(asking.getByLabel(t("vehicles.lifecycle_reason"))).toBeVisible();
  await asking.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  await page.getByRole("button", { name: t("vehicles.delete_button"), exact: true }).click();
  await expect(asking).toContainText(t("vehicles.delete_is_archive"));
  await asking.getByRole("button", { name: t("vehicles.delete_ok"), exact: true }).click();
  await expect(asking.getByLabel(t("vehicles.location"))).toHaveCount(0);
  await asking.getByLabel(t("vehicles.lifecycle_reason")).fill("Not our client any more");
  await asking.getByRole("button", { name: t("vehicles.archive_confirm"), exact: true }).click();
  await expect
    .poll(async () => ((await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as { state: string }).state)
    .toBe("archived");

  // Two cards of the company that match are merged by the company itself, from the conflict
  // shown in the card — the verification of c4rsys decides the matches between companies only
  // (customer's decision of 23.11.2026).
  const make = async (name: string, frame: string) =>
    (
      await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
        name,
        location_id: s.location,
        odometer_unit: "km",
        relation: "managed",
        vehicle_type: "motorcycle",
        frame_number: frame,
      })
    ).vehicle as { id: string; version: number };
  const first = await make("Forza old", "MLHNF0975S5339652");
  // A second card of the same vehicle is not made by a creation: it appears when an identifier
  // of another card is corrected to the same value.
  const second = await make("Forza new", "MLHNF0000000000");
  await ok(page, "PUT", `/c/${s.cmp}/vehicles/${second.id}/details`, {
    expected_version: second.version,
    name: "Forza new",
    vehicle_type: "motorcycle",
    frame_number: "MLHNF0975S5339652",
  });

  await page.goto(`/c/${s.cmp}/vehicles/${first.id}`);
  const conflicts = page.locator(".card", { hasText: t("vehicles.conflicts_title") });
  await expect(conflicts).toContainText(t("vehicles.conflicts_hint_own"));
  await conflicts.getByRole("link", { name: t("vehicles.conflict_merge"), exact: true }).click();
  const merge = page.locator(".card", { hasText: t("vehicles.merge_title") });
  await merge.getByRole("radio").first().check();
  const sentBefore = await mailCode(email);
  await merge.getByRole("button", { name: t("vehicles.merge_send_code"), exact: true }).click();
  await merge.getByLabel(t("vehicles.merge_code")).fill(await mailCode(email, sentBefore));
  await merge.getByLabel(t("vehicles.merge_reason")).fill("One bike");
  await merge.getByRole("button", { name: t("vehicles.merge_submit"), exact: true }).click();
  // The duplicate is merged into the main card and the conflict is closed.
  await expect(page.getByRole("main")).toContainText("«Forza new» → «Forza old»");
  const merged = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${second.id}`)).vehicle as { state: string };
  expect(merged.state).toBe("merged");
  const main = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${first.id}`)).vehicle as {
    identifier_conflicts: { open: boolean }[];
  };
  expect(main.identifier_conflicts.filter((c) => c.open)).toEqual([]);
});
