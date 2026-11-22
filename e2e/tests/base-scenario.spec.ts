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

  // The block «Telegram» (customer's decisions of 22.11.2026) is not shown while the bot is not
  // connected; connected, it makes a one-time link to the bot. The bot is not connected here:
  // its answers are put in their place.
  await expect(page.getByRole("heading", { name: "Telegram", exact: true })).toHaveCount(0);
  await page.route("**/api/me/telegram", (route) =>
    route.fulfill({ json: { configured: true, linked: false, phone: "", username: "" } }),
  );
  await page.route("**/api/me/telegram/link", (route) =>
    route.fulfill({ json: { url: "https://t.me/test_bot?start=one-time" } }),
  );
  await page.reload();
  const telegram = card(page, "Telegram");
  await expect(telegram).toContainText(t("telegram.not_linked"));
  await telegram.getByRole("button", { name: t("telegram.connect"), exact: true }).click();
  await expect(telegram.getByRole("link", { name: t("telegram.open_bot") })).toHaveAttribute(
    "href",
    "https://t.me/test_bot?start=one-time",
  );
  await page.unroute("**/api/me/telegram");
  await page.unroute("**/api/me/telegram/link");

  // Company, its currency, location with the rental context, cash desk.
  await page.goto("/companies");
  const create = card(page, t("companies.create_title"));
  await create.getByLabel(t("companies.display_name")).fill("Тест Аренда");
  await create.getByLabel(t("companies.timezone")).selectOption("Asia/Bangkok");
  await create.getByRole("button", { name: t("companies.create") }).click();
  await expect(page).toHaveURL(/\/c\/CMP-[A-Z0-9]+\/setup$/);
  const base = new URL(page.url()).pathname.replace(/\/setup$/, "");

  // The settings of the company are a tab of «Company»; the old address leads there.
  await page.goto(`${base}/settings/company`);
  await expect(page).toHaveURL(/\/company\/settings$/);
  await expect(page.getByRole("tab", { name: t("company.tab_settings") })).toHaveAttribute("aria-selected", "true");
  // The settings open closed: nothing changes until «Edit», and saving closes them again.
  await expect(page.getByLabel(t("companies.main_currency"))).toBeDisabled();
  await page.getByRole("main").locator("form").getByRole("button", { name: t("common.edit"), exact: true }).click();
  await page.getByLabel(t("companies.main_currency")).selectOption("THB");
  // Several phones of the company, each with whose it is; a new row appears by itself.
  await page.getByLabel(`${t("company.phone")} 1`).fill("+66 80 000 0001");
  await page.getByLabel(`${t("company.phone_label")} 1`).fill("Директор");
  // One row at first; a further number is added by the button.
  await expect(page.getByLabel(`${t("company.phone")} 2`)).toHaveCount(0);
  await page.getByRole("button", { name: `+ ${t("company.phone_add")}` }).click();
  await page.getByLabel(`${t("company.phone")} 2`).fill("+66 80 000 0002");
  await page.getByLabel(`${t("company.phone_label")} 2`).fill("Бухгалтер");
  await page.getByRole("main").locator("form").getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByText(t("common.saved"))).toBeVisible();
  await expect(page.getByLabel(t("companies.main_currency"))).toBeDisabled();

  // A new company has no application: «Rental» is switched on in the tab «Applications» of the
  // company (decision of 16.11.2026); without locations it is only connected.
  await page.goto(`${base}/company/apps`);
  await expect(page.getByRole("tab", { name: t("company.tab_apps") })).toHaveAttribute("aria-selected", "true");
  const rentalApp = page.getByRole("checkbox", { name: `${t("contexts.rental")} — ${t("apps.enabled")}` });
  await expect(rentalApp).not.toBeChecked();
  await rentalApp.click();
  await expect(rentalApp).toBeChecked();

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
  await page.getByRole("combobox", { name: t("cash.currency") }).selectOption("THB");
  await page.getByRole("button", { name: t("wizard.next"), exact: true }).click();
  await page.getByRole("textbox", { name: t("cash.name"), exact: true }).fill("Касса Пхукет");
  await page.getByRole("button", { name: t("cash.create"), exact: true }).click();
  await expect(card(page, t("cash.title")).getByRole("cell", { name: "Касса Пхукет" })).toBeVisible();

  // The window of the Telegram bot (customer's decision of 23.11.2026): `/tg` signs in by the
  // launch data of Telegram and opens the page asked for. Telegram is not here: its launch data
  // and the answer of the server are put in their place; a page of another site is not opened.
  let launch = "";
  await page.route("**/api/telegram/webapp/session", async (route) => {
    launch = (route.request().postDataJSON() as { init_data: string }).init_data;
    await route.fulfill({ json: { status: "signed_in" } });
  });
  await page.goto(`/tg?to=${base}/inspections#tgWebAppData=${encodeURIComponent("user=1&hash=abc")}`);
  // The window shows the short version of the site (customer's decision of 23.11.2026): a page
  // that is not its own leads to its menu, and there is no menu of the site.
  await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  expect(launch).toBe("user=1&hash=abc");
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(page.locator(".topbar")).toHaveCount(0);
  for (const closed of ["/rentals", "/finance", "/settings", "/vehicles"]) {
    await page.goto(`${base}${closed}`);
    await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  }
  await page.goto("/profile");
  await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  await page.getByRole("link", { name: t("miniapp.add_receipt"), exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/app/receipts$`));
  await expect(page.getByLabel(t("drafts.add"))).toBeAttached();
  await page.getByRole("link", { name: `← ${t("miniapp.menu")}` }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  // The menu of the window: with one company it opens at once; a vehicle, a delivery note, a
  // receipt and the inspections — all of them, to be made and made, in two tabs.
  await page.goto("/tg?to=/app#tgWebAppData=x");
  await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  for (const key of ["add_vehicle", "add_receipt", "inspections"]) {
    await expect(page.getByRole("link", { name: t(`miniapp.${key}`), exact: true })).toBeVisible();
  }
  await page.getByRole("link", { name: t("miniapp.inspections"), exact: true }).click();
  await expect(page.getByRole("tab", { name: t("miniapp.tab_waiting") })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(t("miniapp.none_waiting"))).toBeVisible();
  await page.getByRole("tab", { name: t("miniapp.tab_done") }).click();
  await expect(page.getByText(t("miniapp.none_done"))).toBeVisible();
  await page.goto(`/tg?to=https://example.com/#tgWebAppData=x`);
  await expect(page).not.toHaveURL(/example\.com/);
  await expect(page).toHaveURL(new RegExp(`${base}/app$`));
  await page.unroute("**/api/telegram/webapp/session");
  // The window is closed: the full site again.
  await page.evaluate(() => window.sessionStorage.removeItem("c4r-mini"));

  // Drafts of expenses (customer's decisions of 23.11.2026): a receipt sent to the Telegram bot is
  // a draft of its author in «Finance». The bot is not connected here: a draft is put in its place.
  await page.route("**/expense-drafts", (route) =>
    route.fulfill({
      json: {
        drafts: [
          {
            id: "0190a000-0000-7000-8000-00000000d001", file_id: "0190a000-0000-7000-8000-00000000f001",
            spent_on: "2026-11-20", amount: "1250.5", currency: "THB", seller: "PTT Station",
            purpose: "Fuel", created_at: "2026-11-20T10:00:00Z", own: false, author: "Иван Водитель",
          },
        ],
      },
    }),
  );
  await page.goto(`${base}/finance?tab=drafts`);
  await expect(page.getByLabel(t("drafts.add"))).toBeAttached(); // a receipt is added here too
  const draft = page.locator(".expense-draft");
  await expect(draft).toContainText("PTT Station");
  await expect(draft.getByRole("link", { name: t("drafts.receipt"), exact: true })).toBeVisible();
  await expect(draft.getByLabel(t("payments.amount", { currency: "THB" }))).toHaveValue("1250.5");
  // The expense takes the date of the receipt; a draft of another employee names its author.
  await expect(draft.getByLabel(t("drafts.date"))).toHaveValue("2026-11-20");
  await expect(draft).toContainText(t("drafts.author", { name: "Иван Водитель" }));
  await expect(draft.getByLabel(t("money.location"))).toHaveCount(0);
  const recordIt = draft.getByRole("button", { name: t("drafts.record"), exact: true });
  await expect(recordIt).toBeDisabled(); // the article is not chosen yet
  await draft.getByLabel(t("money.article")).selectOption({ index: 1 });
  await expect(recordIt).toBeEnabled();
  await page.unroute("**/expense-drafts");

  // Vehicle, daily tariff 1 000 THB from 1 day, terms «unlimited, no deposit».
  await page.goto(`${base}/vehicles`);
  // A paper of the vehicle is read into the steps (customer's decision of 22.11.2026). The outer
  // service is not connected here: its answer is put in its place.
  await page.route("**/api/catalog/vehicle-options", async (route) => {
    const answer = await route.fetch();
    await route.fulfill({ response: answer, json: { ...(await answer.json()), paper_recognition: true } });
  });
  await page.route("**/vehicles/recognize", (route) =>
    route.fulfill({
      json: {
        vehicle_type: "passenger_car", make: "Toyotaa", model: "Yariss", year: 2022, plate: "AB 1234",
        plate_country: "TH", vin: "MR2K29F30N1234567", body_number: "", chassis_number: "",
        frame_number: "", engine_number: "3NR-1234567", color: "White", body_type: "sedan",
        fuel_type: "petrol", transmission: "", drive: "", engine_volume_cc: 1197, power_hp: null,
        seats: 5, doors: null, curb_weight_kg: null, gross_weight_kg: null,
        make_code: null, model_code: null, catalog_type: null, owner_name: "Somchai Jaidee", owner_kind: "person",
      },
    }),
  );
  // The button «Check on the site» of the Telegram bot leads to the same form with the paper the
  // bot has kept: the steps are filled without an upload.
  await page.goto(`${base}/vehicles/new?paper=0190a000-0000-7000-8000-000000000000`);
  await expect(page.locator(".paper-read")).toContainText("3NR-1234567");
  await page.goto(`${base}/vehicles`);
  await page.getByRole("link", { name: t("vehicles.create_button") }).click();
  await page.locator(".paper-reading input[type=file]").setInputFiles({
    name: "book.png",
    mimeType: "image/png",
    buffer: Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)]),
  });
  const paper = page.locator(".paper-read");
  await expect(paper).toContainText("White");
  await expect(paper).toContainText("3NR-1234567");
  await expect(paper).toContainText(t("vehicles.fuel.petrol"));
  await expect(paper).toContainText(t("vehicles.paper_owner", { name: "Somchai Jaidee" }));
  // A value read wrong is taken out; the rest is written with the card.
  await paper.locator(".paper-fact", { hasText: t("vehicles.seats") }).getByRole("button").click();
  await expect(paper.locator(".paper-fact", { hasText: t("vehicles.seats") })).toHaveCount(0);
  const made = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/vehicles"));
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
  const sent = (await made).postDataJSON() as Record<string, unknown>;
  expect(sent.color).toBe("White");
  expect(sent.engine_volume_cc).toBe(1197);
  expect(sent.seats).toBeUndefined();
  await expect(page.getByRole("heading", { name: "Toyota Yaris" })).toBeVisible();

  // A tariff is created step by step: name, currency, unit (a day from the issue), the ranges
  // without a price, availability, then the vehicles — the marked one gets its price of the
  // range; mileage and deposit are left empty: no limit, no deposit. Its own page opens.
  await page.goto(`${base}/rentals?tab=catalog`);
  await page.getByRole("link", { name: t("rentals.action_tariff") }).click();
  const onward = page.getByRole("button", { name: t("wizard.next"), exact: true });
  await page.getByRole("textbox", { name: t("tariffs.name"), exact: true }).fill("Сутки");
  await onward.click();
  await page.getByRole("combobox", { name: t("cash.currency") }).selectOption("THB");
  await onward.click();
  // The billing is chosen by hand: by the day, a day of 24 hours from the issue.
  await page.getByRole("radio", { name: t("tariffs.billing_day"), exact: true }).check();
  await page.getByRole("radio", { name: t("tariffs.day_from_issue"), exact: true }).check();
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

  // A click on the bell shows the latest notifications in a popup, without leaving the screen.
  const here = page.url();
  await page.getByRole("button", { name: t("notifications.title") }).click();
  const popup = page.getByRole("dialog", { name: t("notifications.title") });
  await expect(popup).toContainText(t("notifications.none"));
  await expect(page).toHaveURL(here);
  // «Open all» leads to the page with the filters, inside the company.
  await popup.getByRole("link", { name: t("notifications.open_all") }).click();
  await expect(page).toHaveURL(new RegExp(`${base}/notifications$`));
  // One company: no switch between «this company» and «all», only the list.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(t("notifications.title"));
  await expect(page.getByRole("button", { name: t("notifications.scope_this") })).toHaveCount(0);
  await expect(page.getByRole("link", { name: t("nav.rentals"), exact: true })).toBeVisible();

  // A rental from 3 days ago 10:00 to 1 day ago 10:00; issued with odometer 10 000, fuel 8.
  await page.goto(`${base}/rentals/new`);
  await choose(page.getByRole("combobox", { name: t("rentals.vehicle"), exact: true }), "Toyota Yaris");
  await choose(page.getByRole("combobox", { name: t("rentals.tariff"), exact: true }), "Сутки");
  await page.getByLabel(t("rentals.find_client")).fill("Иван Петров");
  await page.getByRole("button", { name: /Иван Петров ·/ }).click();
  // The rental is created first: the period, the cost counted by itself, no issue yet.
  await page.getByLabel(t("bookings.planned_start")).fill(issueAt);
  await page.getByLabel(t("rentals.planned_end")).fill(returnAt);
  await expect(card(page, t("rentals.calculation"))).toContainText(`2 ${t("rentals.days")} × 1000 THB = 2000.00 THB`);
  // «Without an inspection»: no inspection is created, the readings are given at the issue.
  await page.getByRole("switch", { name: t("rentals.issue_inspection") }).uncheck();
  await page.getByRole("button", { name: t("vehicles.action_rental"), exact: true }).click();
  await expect(page).toHaveURL(/\/rentals\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("rentals.state_awaiting"));
  // The issue is confirmed from the created rental, with the readings of the handover.
  await page.getByRole("link", { name: t("rentals.issue"), exact: true }).click();
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
    `${t("rentals.calc_actual")}: 2 ${t("rentals.days")} × 1000 THB = 2000.00 THB`,
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
  // The picture of the type of the vehicle stands before its name.
  await expect(page.getByRole("img", { name: t("vehicles.type_passenger_car") })).toBeVisible();
  await expect(page.getByRole("link", { name: t("nav.garage"), exact: true })).toHaveCount(0);
  // The menu (decision of 14.11.2026): productivity, finance, rental, settings; the orders are a tab of
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
  // The tabs of a section stay in place when its pages are switched; the page of the orders
  // draws them anew, so the place is looked at until they stand.
  await expect.poll(async () => (await moneyTab.boundingBox())?.x).toBe(before?.x);
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

  // The card of the client shows text; the data are changed in a window opened by the pencil.
  await page.goto(`${base}/clients`);
  await page.getByRole("link", { name: "Иван Петров" }).click();
  await expect(page.getByRole("main").locator("input")).toHaveCount(0);
  await page.getByRole("button", { name: t("common.edit"), exact: true }).click();
  const editing = page.getByRole("dialog");
  await editing.getByLabel(t("cards.field_residence_address")).fill("Пхукет, Раваи");
  await editing.getByLabel(t("profile.middle_name"), { exact: true }).fill("Сергеевич");
  await editing.getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("dl.facts").first()).toContainText("Пхукет, Раваи");
  // The head names the person as «first name, patronymic, last name».
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Иван Сергеевич Петров");
  // The link with the profile is made on the tab «More», not here.
  await expect(card(page, t("links.title"))).toHaveCount(0);
  // Documents: the driving licence first, its categories from a list, the experience by a year.
  await page.getByRole("button", { name: `${t("common.edit")}: ${t("cards.documents_title")}` }).click();
  await editing.getByLabel(t("cards.series_number")).first().fill("99 12 345678");
  await editing.getByLabel(t("cards.driving_since_year")).fill("2010");
  await editing.getByLabel(t("cards.categories")).fill("B");
  // A further document: its kind from a list; «other» asks for a name.
  await editing.getByRole("button", { name: `+ ${t("cards.add_document")}` }).click();
  await editing.getByLabel(`${t("cards.series_number")} 1`).fill("75 1234567");
  await editing.getByRole("button", { name: `+ ${t("cards.add_document")}` }).click();
  await editing.getByLabel(`${t("cards.document_type")} 2`).selectOption("other");
  await editing.getByLabel(`${t("cards.document_title")} 2`).fill("Страховка");
  await editing.getByLabel(`${t("cards.issued_by")} 2`).fill("AXA");
  await editing.getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const documents = card(page, t("cards.documents_title"));
  await expect(documents).toContainText("99 12 345678");
  await expect(documents).toContainText("2010");
  await expect(documents).toContainText("AXA");
  await expect(documents).toContainText("75 1234567");
  // Finance: the debt, the overpayment and the deposit; what was charged and paid, with links.
  await page.getByRole("tab", { name: t("cards.tab_finance") }).click();
  await expect(card(page, t("cards.money_now"))).toContainText(t("cards.money_deposit"));
  const money = card(page, t("cards.money_title"));
  await expect(money.getByRole("row").filter({ hasText: t("cards.money_charge") })).toHaveCount(1);
  await expect(money.getByRole("link", { name: /№/ })).toHaveCount(2);
  // History: what the client did — the rental with its vehicle, not the order as such.
  await page.getByRole("tab", { name: t("cards.tab_history") }).click();
  const rented = page.getByRole("row").filter({ hasText: t("cards.kind_rental") });
  await expect(rented).toHaveCount(1);
  await expect(rented).toContainText("Toyota Yaris");
  await page.getByRole("tab", { name: t("cards.tab_more") }).click();
  await expect(card(page, t("links.title")).getByRole("button", { name: t("links.start") })).toBeVisible();
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
