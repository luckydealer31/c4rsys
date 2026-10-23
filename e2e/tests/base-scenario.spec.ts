import { expect, test, type Locator, type Page } from "@playwright/test";

import { mailCode, restart } from "../lib/docker";
import { t } from "../lib/i18n";

// Acceptance criteria 1 and 8 (docs/stage1/STAGE1_ACCEPTANCE_TESTS.md): the base example
// through the interface, from registration to closing, with a restart of the database and
// the server before the payment. The dates of the example are moved into the past (decision
// 23.10.2026): issue 3 days ago at 10:00 Asia/Bangkok, return exactly 48 hours later.

const email = `director+${Date.now()}@c4rsys.test`;
const password = `E2e-${Math.random().toString(36).slice(2)}-pass`;
const author = { first: "Анна", last: "Петрова" };

function bangkokDate(daysAgo: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(
    new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000),
  );
}

const issueAt = `${bangkokDate(3)}T10:00`;
const returnAt = `${bangkokDate(1)}T10:00`;

function card(page: Page, title: string): Locator {
  return page.locator("section.card", { has: page.getByRole("heading", { name: title, exact: true }) });
}

/** Selects the option containing `text` once it has loaded. */
async function choose(select: Locator, text: string) {
  const option = select.locator("option", { hasText: text });
  await expect(option).toHaveCount(1);
  await select.selectOption((await option.getAttribute("value")) ?? "");
}

async function confirmCode(page: Page, previous = ""): Promise<string> {
  const code = await mailCode(email, previous);
  await page.getByLabel(t("auth.code")).fill(code);
  await page.getByRole("button", { name: t("auth.confirm") }).click();
  return code;
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel(t("auth.email")).fill(email);
  await page.getByRole("button", { name: t("auth.get_code") }).click();
  await confirmCode(page, await mailCode(email).catch(() => ""));
  await page.getByLabel(t("auth.password")).fill(password);
  await page.getByRole("button", { name: t("auth.sign_in") }).click();
}

async function waitForServer(page: Page) {
  await expect
    .poll(async () => (await page.request.get("/api/health").catch(() => null))?.status() ?? 0, {
      timeout: 120_000,
      intervals: [1000],
    })
    .toBe(200);
}

test("base example from registration to closing survives a restart", async ({ page }) => {
  await waitForServer(page);

  // Registration with the mail code, then the name (§ 2.1.3).
  await page.goto("/register");
  await page.getByLabel(t("auth.email")).fill(email);
  await page.getByRole("button", { name: t("auth.get_code") }).click();
  await confirmCode(page);
  await page.getByLabel(t("auth.new_password")).fill(password);
  await page.getByRole("button", { name: t("auth.create_account") }).click();
  await page.getByRole("button", { name: t("auth.backup_done") }).click();
  await expect(page).toHaveURL(/\/profile$/);
  const name = card(page, t("profile.personal_title"));
  await name.getByLabel(t("profile.first_name")).fill(author.first);
  await name.getByLabel(t("profile.last_name")).fill(author.last);
  await name.getByRole("button", { name: t("common.save") }).click();
  await expect(name.getByText(t("common.saved"))).toBeVisible();

  // Company, its currency, location with the rental context, cash desk.
  await page.goto("/companies");
  const create = card(page, t("companies.create_title"));
  await create.getByLabel(t("companies.display_name")).fill("Тест Аренда");
  await create.getByLabel(t("companies.timezone")).selectOption("Asia/Bangkok");
  await create.getByRole("button", { name: t("companies.create") }).click();
  await expect(page).toHaveURL(/\/c\/CMP-[A-Z0-9]+\/setup$/);
  const base = new URL(page.url()).pathname.replace(/\/setup$/, "");

  await page.goto(`${base}/settings/company`);
  await page.getByLabel(t("companies.main_currency")).selectOption("THB");
  await page.getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByText(t("common.saved"))).toBeVisible();

  await page.goto(`${base}/company`);
  await page.getByRole("button", { name: t("locations.add") }).click();
  const location = card(page, t("locations.create_title"));
  await location.getByLabel(t("locations.name")).fill("Пхукет");
  await location.getByLabel(t("companies.timezone")).selectOption("Asia/Bangkok");
  await location.getByRole("checkbox", { name: t("contexts.rental") }).check();
  await location.getByRole("button", { name: t("locations.create") }).click();
  await expect(page.getByRole("heading", { name: "Пхукет" })).toBeVisible();

  await page.goto(`${base}/finance`);
  const desk = card(page, t("cash.create_title"));
  await desk.getByLabel(t("cash.name")).fill("Касса Пхукет");
  await desk.getByLabel(t("cash.type")).selectOption("cash");
  await desk.getByLabel(t("cash.currency")).selectOption("THB");
  await desk.getByRole("button", { name: t("cash.create") }).click();
  await expect(card(page, t("cash.title")).locator("input[value='Касса Пхукет']")).toBeVisible();

  // Vehicle, daily tariff 1 000 THB from 1 day, terms «unlimited, no deposit».
  await page.goto(`${base}/vehicles`);
  const vehicle = card(page, t("vehicles.create_title"));
  await vehicle.getByLabel(t("vehicles.name")).fill("Toyota Yaris");
  await vehicle.getByLabel(t("vehicles.location")).selectOption({ label: "Пхукет" });
  await vehicle.getByRole("button", { name: t("vehicles.create") }).click();
  await expect(page.getByRole("link", { name: "Toyota Yaris" })).toBeVisible();

  await page.goto(`${base}/settings/tariffs`);
  const tariff = card(page, t("tariffs.create_title"));
  await tariff.getByLabel(t("tariffs.name")).fill("Сутки");
  await tariff.getByLabel(t("cash.currency")).selectOption("THB");
  await tariff.locator("tbody tr").first().locator("input").nth(2).fill("1000");
  await tariff.getByRole("button", { name: t("tariffs.create") }).click();
  await page.getByRole("link", { name: /Сутки/ }).click();
  await choose(page.getByRole("combobox", { name: t("terms.vehicle"), exact: true }), "Toyota Yaris");
  await page.getByLabel(t("terms.mileage")).selectOption("unlimited");
  await page.getByLabel(t("terms.deposit")).selectOption("none");
  await page.getByRole("button", { name: t("terms.assign") }).click();
  await expect(page.getByRole("row", { name: /Toyota Yaris/ })).toBeVisible();

  await page.goto(`${base}/clients`);
  const client = card(page, t("clients.create_title"));
  await client.getByLabel(t("profile.first_name"), { exact: true }).fill("Иван");
  await client.getByLabel(t("profile.last_name"), { exact: true }).fill("Петров");
  await client.getByLabel(t("clients.phone")).fill("+66 81 234 5678");
  await client.getByRole("button", { name: t("clients.create") }).click();
  await expect(page.getByRole("link", { name: "Иван Петров" })).toBeVisible();

  // Issue: 3 days ago at 10:00, odometer 10 000, fuel 8.
  await page.goto(`${base}/rentals/new`);
  await choose(page.getByRole("combobox", { name: t("rentals.vehicle"), exact: true }), "Toyota Yaris");
  await choose(page.getByRole("combobox", { name: t("rentals.tariff"), exact: true }), "Сутки");
  await page.getByLabel(t("rentals.find_client")).fill("Иван Петров");
  await page.getByRole("button", { name: t("rentals.search") }).click();
  await page.getByRole("button", { name: /Иван Петров ·/ }).click();
  await page.getByLabel(t("rentals.actual_start")).fill(issueAt);
  await page.getByLabel(t("rentals.odometer", { unit: "km" })).fill("10000");
  await page.getByLabel(t("rentals.fuel")).fill("8");
  await page.getByRole("button", { name: t("rentals.confirm_issue") }).click();
  await expect(page).toHaveURL(/\/rentals\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_active"));

  // Return 48 hours later with the same fuel, then the formalization.
  const back = card(page, t("rentals.block_returned"));
  await back.getByLabel(t("rentals.actual_end")).fill(returnAt);
  await back.getByLabel(t("rentals.odometer", { unit: "" }).trim()).fill("10300");
  await back.getByLabel(t("rentals.fuel")).fill("8");
  await back.getByRole("button", { name: t("rentals.accept_return") }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_returned"));
  await expect(card(page, t("rentals.calculation"))).toContainText(
    `${t("rentals.calc_actual")}: 2880 ${t("rentals.minutes")} → 2 ${t("rentals.days")} → 1000 → 2000.00 THB`,
  );
  await expect(page.getByText(t("rentals.fuel_same"))).toBeVisible();
  await page.getByRole("button", { name: t("rentals.complete_formalization") }).click();
  await expect(card(page, t("rentals.block_formalization"))).toContainText(
    t("rentals.formalization_done", { when: "" }).replace(/\.$/, "").trim(),
  );
  const rentalUrl = page.url();

  // Criterion 8: the database and the server restart; the data stays, the work goes on.
  await restart("db");
  await restart("backend");
  await waitForServer(page);
  await page.goto(rentalUrl);
  if (new URL(page.url()).pathname === "/login") {
    await signIn(page);
    await page.goto(rentalUrl);
  }
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_returned"));

  // Order: 2 000 to pay, cash 2 000 by default, closing.
  await page.getByRole("link", { name: t("rentals.open_order") }).first().click();
  await expect(page).toHaveURL(/\/orders\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`${t("orders.order")} № 1`);
  const amounts = (paid: string, left: string) =>
    `${t("orders.charged")} 2000.00 · ${t("orders.paid")} ${paid} · ${t("orders.to_pay")} ${left} THB`;
  await expect(page.locator("dl.facts")).toContainText(amounts("0.00", "2000.00"));
  const receipt = card(page, t("payments.receive_title"));
  await expect(receipt.getByLabel(t("payments.amount", { currency: "THB" }))).toHaveValue("2000.00");
  await receipt.getByRole("button", { name: t("payments.receive"), exact: true }).click();
  await expect(page.locator("dl.facts")).toContainText(amounts("2000.00", "0.00"));
  await page.getByRole("button", { name: t("orders.close") }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("orders.state_closed"));

  // History with authors: issue, return, charge, payment, closing.
  await page.getByRole("button", { name: t("rentals.history") }).click();
  const history = card(page, t("rentals.history"));
  for (const action of [
    "actions.rental_issued",
    "actions.rental_returned",
    "actions.charge_recorded",
    "actions.payment_received",
    "actions.order_closed",
  ]) {
    await expect(history.getByRole("row").filter({ hasText: t(action) }).first()).toContainText(
      `${author.first} ${author.last}`,
    );
  }
  await expect(history.getByRole("row").filter({ hasText: t("actions.charge_recorded") })).toHaveCount(1);
  await expect(history.getByRole("row").filter({ hasText: t("actions.payment_received") })).toHaveCount(1);
});
