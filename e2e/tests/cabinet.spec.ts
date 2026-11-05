import { expect, test, type Page } from "@playwright/test";

import { bangkokIso, createCompany, newPassword, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { mailCode } from "../lib/docker";
import { t } from "../lib/i18n";

// Audit item 41: the client in the personal cabinet (decision of 30.10.2026: its own domain with
// its own sign-in). The client registers on the cabinet domain, accepts the company's request
// to link the client card (§ 6.2) and sees the rental of that company in the history.

/** The cabinet domain of the stack: `cabinet.localhost` on the same port (C4R_CABINET_HOSTS). */
function cabinetUrl(base: string): string {
  const url = new URL(base);
  url.hostname = "cabinet.localhost";
  return url.origin;
}

async function registerInInterface(page: Page, email: string) {
  await page.goto("/register");
  await page.getByLabel(t("auth.email")).fill(email);
  await page.getByRole("button", { name: t("auth.get_code") }).click();
  await page.getByLabel(t("auth.code")).fill(await mailCode(email));
  await page.getByRole("button", { name: t("auth.confirm") }).click();
  await page.getByLabel(t("auth.new_password")).fill(newPassword());
  await page.getByRole("button", { name: t("auth.create_account") }).click();
  await page.getByRole("button", { name: t("auth.backup_done") }).click();
}

test("a client links the card in the cabinet and sees the rental", async ({ page, browser, baseURL }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("director"), "Анна", "Петрова");
  const s = await createCompany(page);
  await ok(page, "POST", `/c/${s.cmp}/rentals/issue`, {
    vehicle_card_id: s.vehicle,
    client_id: s.client,
    tariff_id: s.tariff,
    issue_location_id: s.location,
    actual_start: bangkokIso(-1, "10:00"),
    odometer: "10000",
    fuel: "8",
  });

  // The client: another browser on the cabinet domain.
  const cabinet = await browser.newContext({
    baseURL: cabinetUrl(baseURL ?? "http://localhost:5173"),
    locale: "ru-RU",
    timezoneId: "Asia/Bangkok",
  });
  const client = await cabinet.newPage();
  const clientEmail = uniqueEmail("client");
  await registerInInterface(client, clientEmail);
  await client.goto("/");
  await expect(client.getByRole("heading", { level: 1 })).toHaveText(t("cabinet.companies"));
  await expect(client.getByText(t("cabinet.no_companies"))).toBeVisible();

  // The company asks to link its client card to this profile.
  await ok(page, "POST", `/c/${s.cmp}/profile-links`, { card_type: "client", card_id: s.client, target: clientEmail });

  await client.reload();
  const request = client.locator("section.card", { has: client.getByRole("heading", { name: "Тест Аренда" }) });
  await expect(request).toContainText(t("links.asks_client"));
  await request.getByRole("button", { name: t("links.accept") }).click();

  // The linked company and its history: the order with the rental of Toyota Yaris.
  await client.getByRole("link", { name: /Тест Аренда/ }).click();
  await expect(client).toHaveURL(/\/companies\/[0-9a-f-]+$/);
  await expect(client.getByText(t("cabinet.order", { number: 1 }))).toBeVisible();
  await expect(client.getByText(`${t("cabinet.op_rental")}: Toyota Yaris`)).toBeVisible();

  // The company interface is not served on the cabinet domain.
  await client.goto(`${s.base}/rentals`);
  await expect(client.getByRole("heading", { level: 1 })).toHaveText(t("cabinet.companies"));
  await cabinet.close();
});
