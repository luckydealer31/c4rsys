import { expect, type Page } from "@playwright/test";

import { ok, uniqueEmail, type Company } from "./api";
import { mailCode } from "./docker";
import { t } from "./i18n";

// Customer's decisions of 23–24.11.2026 about the cards of one vehicle in a company: a vehicle
// added as managed that is kept as a client one gets no second card; the management is ended
// and a card is archived, shown in the archive and restored by small buttons with their own
// windows; two matching cards are merged by the company itself. It runs inside a scenario that
// has registered already: the hourly limit of registrations from one address stays as it is.
export async function ownershipScenario(page: Page, s: Company, email: string): Promise<void> {
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
  // The owner of a managed vehicle is an investor (customer's decision of 26.11.2026, in place
  // of the counterparty of 25.11.2026): a person or a company, chosen from the list or entered
  // anew; what is in the list already is shown while it is entered.
  await ok(page, "POST", `/c/${s.cmp}/investors`, {
    kind: "person",
    last_name: "Щукин",
    first_name: "Тимофей",
    middle_name: "Андреевич",
    contacts: [{ kind: "phone", value: "+66 81 555 0101" }],
  });
  const picker = page.getByTestId("owner-picker");
  await picker.locator("select").first().selectOption("person");
  await expect(picker.getByLabel(t("vehicles.owner_last_name"))).toBeVisible();
  await expect(picker.getByLabel(t("vehicles.owner_middle_name"))).toBeVisible();
  await picker.locator("select").first().selectOption("company");
  await expect(picker.getByLabel(t("vehicles.owner_name_company"))).toBeVisible();
  await expect(picker.getByLabel(t("vehicles.owner_contact_person"))).toBeVisible();
  await picker.locator("select").first().selectOption("person");
  // No search of its own and no ID here: the matches come as the last name alone is typed
  // (customer's remark of 25.11.2026).
  await expect(picker.getByRole("textbox")).toHaveCount(5);
  await picker.getByLabel(t("vehicles.owner_last_name")).fill("Щукин");
  const matches = page.getByTestId("owner-matches");
  await expect(matches).toContainText("Щукин Тимофей Андреевич");
  await expect(matches).toContainText(t("clients.matched_last_name"));
  await matches.getByRole("button", { name: t("vehicles.owner_use_existing", { name: "Щукин Тимофей Андреевич" }) }).click();
  await expect(picker.getByRole("link", { name: "Щукин Тимофей Андреевич" })).toBeVisible();
  await expect(picker.getByLabel(t("vehicles.owner_last_name"))).toHaveCount(0);
  await next.click();
  await next.click();
  await page.getByRole("main").locator("select").first().selectOption(s.location);
  await page.getByRole("button", { name: t("vehicles.create"), exact: true }).click();

  const notice = page.getByTestId("was-client");
  await expect(notice).toContainText("Client Forza");
  await notice.getByRole("button", { name: t("vehicles.take_into_management_button"), exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/vehicles/${earlier.id}$`));
  await expect(page.getByRole("heading", { name: "Client Forza" })).toBeVisible();
  const card = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as {
    relation: string;
    owner: { counterparty?: { name: string } };
  };
  expect(card.relation).toBe("managed");
  expect(card.owner.counterparty?.name).toBe("Щукин Тимофей Андреевич");
  // The investors are a tab of their own, apart from the counterparties; the card of an investor
  // shows the vehicles he gave into the management (customer's decision of 26.11.2026).
  await page.goto(`/c/${s.cmp}/clients?tab=counterparties`);
  await expect(page.getByRole("tab", { name: t("clients.tab_investors"), exact: true })).toBeVisible();
  await expect(page.getByRole("main")).toContainText(t("counterparties.none"));
  await page.getByRole("tab", { name: t("clients.tab_investors"), exact: true }).click();
  await expect(page.getByRole("link", { name: t("investors.new_button"), exact: true })).toBeVisible();
  await page.getByRole("link", { name: /Щукин/ }).click();
  await expect(page).toHaveURL(/\/clients\/investors\//);
  await expect(page.getByTestId("investor-vehicles")).toContainText("Client Forza");
  await page.getByTestId("investor-vehicles").getByRole("link", { name: "Client Forza" }).click();
  await expect(page).toHaveURL(new RegExp(`/vehicles/${earlier.id}`));
  // The ownership is a tab of the card of its own (customer's decision of 24.11.2026); the old
  // address of the part of the history leads to it.
  await page.goto(`/c/${s.cmp}/vehicles/${earlier.id}?tab=history&part=ownership`);
  await expect(page.getByRole("tab", { name: t("vehicles.card_ownership"), exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.duplicates_history_title"));
  // «History» is not a tab of the card any more: the journal stands in the settings.
  await expect(page.getByRole("tab", { name: t("vehicles.card_history"), exact: true })).toHaveCount(0);
  for (const key of ["finance", "rentals", "moves"]) {
    await expect(page.getByRole("tab", { name: t(`vehicles.card_${key}`), exact: true })).toBeVisible();
  }
  // The actions with the relation stand in the settings, not in the history (customer's remark
  // of 24.11.2026); every action is a small button that asks what it needs in its own window.
  await expect(page.getByRole("button", { name: t("vehicles.end_management_button"), exact: true })).toHaveCount(0);
  await page.goto(`/c/${s.cmp}/vehicles/${earlier.id}?tab=settings&part=card`);
  await expect(page.getByRole("tab", { name: t("vehicles.settings_card"), exact: true })).toHaveAttribute("aria-selected", "true");
  // A managed vehicle offers the confirmation by the owner in «Access»; what is passed to the
  // owner is set only after the link is confirmed (customer's decision of 24.11.2026).
  await page.getByRole("tab", { name: t("vehicles.settings_access"), exact: true }).click();
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.disclosure_title"));
  // The owner is named by his ID right here, and the request goes at once (customer's remark of
  // 25.11.2026).
  await page.getByLabel(t("owner_requests.lookup")).fill(uniqueEmail("owner"));
  await page.getByRole("button", { name: t("owner_requests.send"), exact: true }).click();
  await expect(page.getByRole("main")).toContainText(t("owner_requests.state_open"));
  await page.getByRole("tab", { name: t("vehicles.settings_card"), exact: true }).click();
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
  // The cards in the archive are not in the lists until asked for; then only they are shown
  // (customer's decision of 24.11.2026).
  await page.goto(`/c/${s.cmp}/vehicles?tab=client`);
  await expect(page.getByText(t("vehicles.none"), { exact: true })).toBeVisible();
  // The fleet and the client vehicles are tabs under the tabs of the section.
  await expect(page.getByRole("tab", { name: t("vehicles.tab_client"), exact: true })).toHaveAttribute("aria-selected", "true");
  // One button on the page puts both the tabs and the tabs under them in order (customer's
  // decision of 24.11.2026).
  const order = page.getByRole("button", { name: t("common.tabs_order"), exact: true });
  await expect(order).toHaveCount(1);
  await order.click();
  await expect(page.locator(".tabs.ordering")).toHaveCount(2);
  await page.getByRole("button", { name: t("common.tabs_order_done"), exact: true }).click();
  await expect(page.locator(".tabs.ordering")).toHaveCount(0);
  await page.getByRole("button", { name: t("vehicles.archive_switch"), exact: true }).click();
  await expect(page.getByRole("link", { name: "Client Forza" })).toBeVisible();
  // A card is restored as what it was archived from, with no choice: a client one asks the
  // reason only.
  await page.getByRole("link", { name: "Client Forza" }).click();
  await page.goto(`${page.url().split("?")[0]}?tab=settings&part=card`);
  await page.getByRole("button", { name: t("vehicles.restore_button"), exact: true }).click();
  await expect(asking.getByLabel(t("vehicles.relation"))).toHaveCount(0);
  await expect(asking.getByLabel(t("vehicles.location"))).toHaveCount(0);
  await asking.getByLabel(t("vehicles.lifecycle_reason")).fill("The client is back");
  await asking.getByRole("button", { name: t("vehicles.restore_button"), exact: true }).click();
  await expect
    .poll(async () => {
      const v = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${earlier.id}`)).vehicle as { state: string; relation: string };
      return `${v.state} ${v.relation}`;
    })
    .toBe("active client");

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
  // A closed conflict leaves the main tab and stays in the history of the ownership.
  await page.goto(`/c/${s.cmp}/vehicles/${first.id}`);
  await expect(page.getByRole("heading", { name: "Forza old" })).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.conflicts_title"));
  await page.goto(`/c/${s.cmp}/vehicles/${first.id}?tab=ownership`);
  // The closed conflict, the merge and the merged card stand in one block; a card without any
  // of them has no such block (customer's remark of 24.11.2026).
  const duplicates = page.locator("section.card", { hasText: t("vehicles.duplicates_history_title") });
  await expect(duplicates).toHaveCount(1);
  await expect(duplicates).toContainText(t("vehicles.conflicts_closed_title"));
  await expect(duplicates).toContainText(t("vehicles.merged_cards_title"));
  // The history of the merged card is unfolded by the wide chevrons under its line.
  await duplicates.getByRole("button", { name: t("vehicles.merged_show"), exact: true }).click();
  await expect(duplicates.getByRole("button", { name: t("vehicles.merged_hide"), exact: true })).toBeVisible();
  await duplicates.getByRole("button", { name: t("vehicles.merged_hide"), exact: true }).click();
  await expect(page.getByRole("heading", { name: t("vehicles.history_title"), exact: true })).toHaveCount(0);
  // The history holds records only: the merge is named there, and it is split in the settings.
  await expect(page.getByRole("main")).toContainText("«Forza new» → «Forza old»");
  await expect(page.getByRole("button", { name: t("vehicles.split_open"), exact: true })).toHaveCount(0);
  await page.goto(`/c/${s.cmp}/vehicles/${first.id}?tab=settings&part=card`);
  await expect(page.getByRole("button", { name: t("vehicles.split_open"), exact: true })).toBeVisible();
}
