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
  // Several phones of the company, each with whose it is; a new row appears by itself.
  await page.getByLabel(`${t("company.phone")} 1`).fill("+66 80 000 0001");
  await page.getByLabel(`${t("company.phone_label")} 1`).fill("Директор");
  await page.getByLabel(`${t("company.phone")} 2`).fill("+66 80 000 0002");
  await page.getByLabel(`${t("company.phone_label")} 2`).fill("Бухгалтер");
  await page.getByRole("main").locator("form").getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByText(t("common.saved"))).toBeVisible();

  await page.goto(`${base}/company`);
  await expect(page.locator("dl.facts")).toContainText("Директор");
  await expect(page.locator("dl.facts")).toContainText("+66 80 000 0002");
  // The card of the company shows the filled fields only; a new location is added in a window.
  await expect(page.locator("dl.facts dd", { hasText: /^—$/ })).toHaveCount(0);
  await page.getByRole("button", { name: t("locations.create_title") }).click();
  const location = page.getByRole("dialog");
  await location.getByLabel(t("locations.name")).fill("Пхукет");
  await location.getByLabel(t("companies.timezone")).selectOption("Asia/Bangkok");
  await location.getByRole("checkbox", { name: t("contexts.rental") }).check();
  await location.getByRole("button", { name: t("locations.create") }).click();
  await expect(page.getByRole("heading", { name: "Пхукет" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Cash desks are on the «Cash desks and accounts» tab of the finance section.
  await page.goto(`${base}/finance?tab=accounts`);
  // A desk is created step by step: type, currency, name.
  await page.getByRole("link", { name: t("cash.create_button") }).click();
  await page.getByRole("radio", { name: t("cash.type_cash") }).check();
  await page.getByRole("button", { name: t("wizard.next"), exact: true }).click();
  await page.getByLabel(t("cash.currency")).selectOption("THB");
  await page.getByRole("button", { name: t("wizard.next"), exact: true }).click();
  await page.getByRole("textbox", { name: t("cash.name"), exact: true }).fill("Касса Пхукет");
  await page.getByRole("button", { name: t("cash.create"), exact: true }).click();
  await expect(card(page, t("cash.title")).getByRole("cell", { name: "Касса Пхукет" })).toBeVisible();

  // Vehicle, daily tariff 1 000 THB from 1 day, terms «unlimited, no deposit».
  await page.goto(`${base}/vehicles`);
  await page.getByRole("link", { name: t("vehicles.create_button") }).click();
  // The creation goes step by step: type, then make, model, year, VIN and plate are skipped.
  const next = page.getByRole("button", { name: t("wizard.next"), exact: true });
  await next.click();
  for (let i = 0; i < 5; i += 1) {
    await page.getByRole("button", { name: t("wizard.skip"), exact: true }).click();
  }
  // Nothing to form the name from, so it is entered by hand; then owner and odometer unit.
  await card(page, t("vehicles.name")).getByRole("textbox").fill("Toyota Yaris");
  await next.click();
  await next.click();
  await next.click();
  await page.getByLabel(t("vehicles.location")).selectOption({ label: "Пхукет" });
  await page.getByRole("button", { name: t("vehicles.create") }).click();
  await expect(page.getByRole("heading", { name: "Toyota Yaris" })).toBeVisible();

  // A tariff is created step by step: name, currency, unit (a day from the issue), the ranges
  // without a price, availability, then the vehicles — the marked one gets its price of the
  // range; mileage and deposit are left empty: no limit, no deposit. Its own page opens.
  await page.goto(`${base}/rentals?tab=catalog`);
  await page.getByRole("link", { name: t("rentals.action_tariff") }).click();
  const onward = page.getByRole("button", { name: t("wizard.next"), exact: true });
  await page.getByRole("textbox", { name: t("tariffs.name"), exact: true }).fill("Сутки");
  await onward.click();
  await page.getByLabel(t("cash.currency")).selectOption("THB");
  await onward.click();
  await onward.click();
  await onward.click();
  await onward.click();
  await page.getByRole("checkbox", { name: "Toyota Yaris" }).check();
  await page.getByRole("textbox", { name: `Toyota Yaris: ${t("terms.price")} 1` }).fill("1000");
  await page.getByRole("button", { name: t("tariffs.create"), exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Сутки");
  await expect(page.getByRole("checkbox", { name: "Toyota Yaris" })).toBeChecked();

  await page.goto(`${base}/clients`);
  // The client is created step by step: kind, the request by the ID is skipped, name, birth
  // date is skipped, contacts.
  await page.getByRole("link", { name: t("clients.new_button") }).click();
  const further = page.getByRole("button", { name: t("wizard.next"), exact: true });
  const skip = page.getByRole("button", { name: t("wizard.skip"), exact: true });
  await further.click();
  await skip.click();
  await page.getByLabel(t("profile.first_name"), { exact: true }).fill("Иван");
  await page.getByLabel(t("profile.last_name"), { exact: true }).fill("Петров");
  await further.click();
  await skip.click();
  await page.getByLabel(t("clients.phone"), { exact: true }).fill("+66 81 234 5678");
  await page.getByRole("button", { name: t("clients.create") }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Иван");
  await page.goto(`${base}/clients`);
  await expect(page.getByRole("link", { name: "Иван Петров" })).toBeVisible();
  // Employees: the list only; the invitation opens in a window by the button «New employee».
  await page.getByRole("tab", { name: t("clients.tab_employees") }).click();
  await expect(page.getByLabel(t("employees.target"))).toHaveCount(0);
  await page.getByRole("button", { name: t("employees.new_button") }).click();
  await expect(page.getByRole("dialog").getByLabel(t("employees.target"))).toBeVisible();
  await page.getByRole("dialog").locator("form").getByRole("button", { name: t("common.cancel") }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

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

  // A document of the vehicle: added in the settings of its card, shown on the main tab.
  await page.goto(`${base}/vehicles`);
  await page.locator('a[href*="/vehicles/"]:not([href*="/vehicles/new"])').first().click();
  await expect(page.getByRole("tab", { name: t("vehicles.card_settings") })).toBeVisible();
  const cardUrl = page.url().split("?")[0];
  // «Back» returns to the list the card was opened from, also after looking through its tabs.
  await page.getByRole("tab", { name: t("vehicles.card_history") }).click();
  await page.getByRole("button", { name: t("common.back"), exact: true }).click();
  await expect(page).toHaveURL(/\/vehicles$/);
  // The garage is a tab of «Company»; the schedule and the trips are tabs of «Rental».
  await expect(page.getByRole("tab", { name: t("company.tab_garage") })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("link", { name: t("nav.garage"), exact: true })).toHaveCount(0);
  // The menu (decision of 14.11.2026): home, finance, rental, settings; the orders are a tab of
  // «Finance», the tasks a tab of «Home», the documents a tab of «Company».
  await expect(page.getByRole("link", { name: t("nav.orders"), exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: t("nav.tasks"), exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: t("nav.documents") }).click();
  await expect(page).toHaveURL(/\/documents$/);
  await page.goto(`${base}/finance`);
  const moneyTab = page.getByRole("tab", { name: t("money.tab_money") });
  const before = await moneyTab.boundingBox();
  await page.getByRole("tab", { name: t("nav.orders") }).click();
  await expect(page).toHaveURL(/\/orders$/);
  // The tabs of a section stay in place when its pages are switched.
  expect((await moneyTab.boundingBox())?.x).toBe(before?.x);
  await expect(page.getByRole("link", { name: t("nav.finance"), exact: true })).toHaveClass(/active/);
  await page.goto(`${base}/dashboards`);
  await page.getByRole("tab", { name: t("nav.tasks") }).click();
  await expect(page).toHaveURL(/\/tasks$/);
  await page.goto(`${base}/vehicles?view=schedule`);
  await expect(page).toHaveURL(/\/rentals\?tab=schedule$/);
  await expect(page.getByRole("tab", { name: t("rentals.tab_schedule") })).toHaveAttribute("aria-selected", "true");
  await page.goto(cardUrl);
  await page.getByRole("tab", { name: t("vehicles.card_settings") }).click();
  await page.getByRole("button", { name: t("vehicles.document_add") }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(t("vehicles.document_name")).fill("Blue Book");
  await dialog.getByLabel(t("vehicles.document_number")).fill("123456");
  await dialog.getByLabel(t("vehicles.document_valid_to")).fill("2027-05-01");
  await dialog.locator("input[type=file]").setInputFiles({ name: "book.pdf", mimeType: "application/pdf", buffer: onePagePdf() });
  await dialog.getByRole("button", { name: t("common.save") }).click();
  await expect(card(page, t("vehicles.documents_title"))).toContainText("Blue Book");
  // The file opens in a window of the interface, as pages drawn in place, not by a link.
  await expect(card(page, t("vehicles.documents_title")).locator("a")).toHaveCount(0);
  await page.getByRole("button", { name: `${t("files.preview_open")}: Blue Book` }).click();
  await expect(page.getByRole("dialog").locator(".file-preview-pages canvas")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Money of the vehicle: the cost of the acquisition is shown and changed by the pencil.
  await page.goto(`${cardUrl}?tab=history&part=finance`);
  await page.getByRole("button", { name: `${t("common.edit")}: ${t("vehicles.acquisition_cost")}` }).click();
  await page.getByRole("dialog").getByLabel(t("vehicles.acquisition_cost")).fill("500000");
  await page.getByRole("dialog").getByLabel(t("vehicles.acquisition_currency")).selectOption("THB");
  await page.getByRole("dialog").getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(card(page, t("vehicles.acquisition"))).toContainText("THB");
  await expect(card(page, t("vehicles.acquisition")).locator("input")).toHaveCount(0);
  // Incomes and expenses go in one list.
  await expect(card(page, t("vehicles.finance_moves"))).toBeVisible();
  // Who added the document is written in the journal of the card.
  await page.goto(`${cardUrl}?tab=history&part=changes`);
  await expect(card(page, t("vehicles.journal_title"))).toContainText(t("vehicles.journal_actions.vehicle_document-added"));
  await page.goto(cardUrl);
  await expect(card(page, t("vehicles.card_main"))).toContainText("Blue Book");
  await expect(card(page, t("vehicles.card_main"))).toContainText("123456");
});

/** A PDF of one page with a coloured rectangle, with a correct table of objects. */
function onePagePdf(): Buffer {
  const content = "0.2 0.4 0.8 rg 20 20 260 160 re f";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << >> >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const start = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) body += `${String(offset).padStart(10, "0")} 00000 n \n`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}
