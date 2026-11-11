import { expect, test, type Locator } from "@playwright/test";

import { call, createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Audit item 41: an employee without a right (§ 23.1, § 23.5). The rental manager of the company
// has the right to issue taken away by a personal restriction: the interface does not offer the
// issue, and the server refuses it when it is sent anyway.

async function choose(select: Locator, text: string) {
  const option = select.locator("option", { hasText: text });
  await expect(option).toHaveCount(1);
  await select.selectOption((await option.getAttribute("value")) ?? "");
}

test("an employee without the right to issue is refused by the server", async ({ page, browser, baseURL }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("director"), "Анна", "Петрова");
  const s = await createCompany(page);

  // The employee: another browser, invited with the rental manager's set minus the issue.
  const own = await browser.newContext({ baseURL, locale: "ru-RU", timezoneId: "Asia/Bangkok", ignoreHTTPSErrors: true });
  const staff = await own.newPage();
  await registerUser(staff, uniqueEmail("manager"), "Олег", "Смирнов");
  const me = (await ok(staff, "GET", "/me")).user as { public_id: string };
  const invitation = (
    await ok(page, "POST", `/c/${s.cmp}/employees/invitations`, {
      target: me.public_id,
      position_name: "Менеджер",
      rights: [
        { type: "set", set: "rental_manager" },
        { type: "deny", permission: "rental.issue" },
      ],
    })
  ).invitation as { id: string };
  await ok(staff, "POST", `/invitations/${invitation.id}/accept`);

  // The list of rentals is open; the issue is not offered.
  await staff.goto(`${s.base}/rentals`);
  await expect(staff.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(staff.getByRole("link", { name: t("vehicles.action_rental"), exact: true })).toHaveCount(0);

  // The issue screen opened by its address: the server refuses, nothing is issued.
  await staff.goto(`${s.base}/rentals/new`);
  await choose(staff.getByRole("combobox", { name: t("rentals.vehicle"), exact: true }), "Toyota Yaris");
  await choose(staff.getByRole("combobox", { name: t("rentals.tariff"), exact: true }), "Сутки");
  await staff.getByLabel(t("rentals.find_client")).fill("Иван Петров");
  await staff.getByRole("button", { name: t("rentals.search") }).click();
  await staff.getByRole("button", { name: /Иван Петров ·/ }).click();
  await staff.getByLabel(t("rentals.odometer", { unit: "km" })).fill("10000");
  await staff.getByLabel(t("rentals.fuel")).fill("8");
  await staff.getByRole("button", { name: t("rentals.confirm_issue") }).click();
  await expect(staff.getByText(t("errors.permission_denied"))).toBeVisible();
  await expect(staff).toHaveURL(/\/rentals\/new$/);

  // The same request straight to the API: 403, and no rental exists.
  const [status, answer] = await call(staff, "POST", `/c/${s.cmp}/rentals/issue`, {
    vehicle_card_id: s.vehicle,
    client_id: s.client,
    tariff_id: s.tariff,
    issue_location_id: s.location,
    odometer: "10000",
    fuel: "8",
  });
  expect(status).toBe(403);
  expect((answer.error as { code: string }).code).toBe("PERMISSION_DENIED");
  const rentals = (await ok(page, "GET", `/c/${s.cmp}/rentals`)).rentals as unknown[];
  expect(rentals).toHaveLength(0);
  await own.close();
});
