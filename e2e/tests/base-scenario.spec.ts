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
  // The documents: none is required; each is added with its kind chosen — here a driving licence
  // with its categories and a passport (customer's remarks of 25–26.11.2026).
  await expect(name.getByTestId("profile-document")).toHaveCount(0);
  const add = name.getByRole("button", { name: `+ ${t("cards.add_document")}` });
  await add.click();
  const licence = name.getByTestId("profile-document").nth(0);
  await expect(licence.getByLabel(t("profile.document_categories"))).toHaveCount(0);
  await licence.getByLabel(t("profile.document_type")).selectOption("driver_license");
  await licence.getByLabel(t("profile.document_number")).fill("7701123456");
  await licence.getByLabel(t("profile.document_categories")).fill("B, C");
  await licence.getByLabel(t("cards.issued_by")).fill("ГИБДД 7701");
  await add.click();
  const passport = name.getByTestId("profile-document").nth(1);
  await passport.getByLabel(t("profile.document_type")).selectOption("passport");
  await passport.getByLabel(t("profile.document_number")).fill("AB 1234567");
  await passport.getByLabel(t("cards.issued_by")).fill("ОВД района Хамовники");
  await name.getByRole("button", { name: t("common.save") }).click();
  await expect(name.getByText(t("common.saved"))).toBeVisible();
  await expect(name).toContainText(t("profile.document_driver_license"));
  await expect(name).toContainText("7701123456 · ");
  await expect(name).toContainText("B, C");
  await expect(name).toContainText(`${t("cards.issued_by")}: ГИБДД 7701`);
  await expect(name).toContainText(t("profile.document_passport"));

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
        make_code: null, model_code: null, catalog_type: null, owner_name: "Somchai Jaidee", owner_last_name: "Jaidee", owner_first_name: "Somchai", owner_middle_name: "", owner_kind: "person",
        power_kw: "63.0", vehicle_category: "B", eco_class: "Euro 5", type_approval: "", special_marks: "",
        owner_address: "Phuket, 1 Beach Rd", paper_name: "Registration book", paper_number: "RB-778",
        paper_issued_on: "2022-05-01", paper_issued_by: "DLT", passport_number: "PS-42", paper_keepable: true, plate_region: "Phuket",
      },
    }),
  );
  // The button «Check on the site» of the Telegram bot leads to the same form with the paper the
  // bot has kept: the steps are filled without an upload.
  await page.goto(`${base}/vehicles/new?paper=0190a000-0000-7000-8000-000000000000`);
  await expect(page.locator(".paper-read").getByLabel(t("vehicles.engine_number"))).toHaveValue("3NR-1234567");
  await page.goto(`${base}/vehicles`);
  await page.getByRole("link", { name: t("vehicles.create_button") }).click();
  // Without a paper the creation goes step by step: type, then make, model, year, VIN and plate
  // may be skipped.
  await page.getByRole("button", { name: t("wizard.next"), exact: true }).click();
  for (let i = 0; i < 5; i += 1) {
    await page.getByRole("button", { name: t("wizard.skip"), exact: true }).click();
  }
  await expect(card(page, t("vehicles.name")).getByRole("textbox")).toBeVisible();
  // Two sides of a paper are chosen at once and kept as one document of two pages; more than
  // five photos are refused (customer's decision of 23.11.2026).
  const side = (name: string) => ({
    name,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    ),
  });
  const chooser = page.locator(".paper-reading input[type=file]");
  await chooser.setInputFiles(["1", "2", "3", "4", "5", "6"].map((n) => side(`${n}.png`)));
  await expect(page.locator(".paper-reading")).toContainText(t("errors.too_many_pages", { max: 5 }));
  const kept = page.waitForResponse((r) => r.url().includes("/api/files?purpose=vehicle_paper") && r.status() === 201);
  await chooser.setInputFiles([side("front.png"), side("back.png")]);
  expect((await (await kept).json()).file.content_type).toBe("application/pdf");
  // After the reading everything stands in one form (customer's decision of 23.11.2026): it is
  // checked, corrected, and the vehicle is made at once, without the steps.
  const paper = page.locator(".paper-read");
  await expect(paper.getByLabel(t("vehicles.color"))).toHaveValue("White");
  await expect(paper.getByLabel(t("vehicles.engine_number"))).toHaveValue("3NR-1234567");
  await expect(paper.getByLabel(t("vehicles.fuel_type"))).toHaveValue("petrol");
  await expect(paper.getByRole("textbox", { name: /^VIN/ })).toHaveValue("MR2K29F30N1234567");
  await expect(paper.getByLabel(t("vehicles.eco_class"))).toHaveValue("Euro 5");
  await expect(paper).toContainText(t("vehicles.paper_owner", { name: "Somchai Jaidee" }));
  await expect(page.getByRole("button", { name: t("wizard.next"), exact: true })).toHaveCount(0);
  // The owner is chosen first, by three short choices; the name read from the paper is put
  // into the owner of a managed vehicle (customer's remark of 23.11.2026).
  await paper.getByRole("radio", { name: t("vehicles.owner_short_managed"), exact: true }).check();
  // A person's name apart (customer's remark of 25.11.2026).
  await expect(paper.getByLabel(t("vehicles.owner_last_name"))).toHaveValue("Jaidee");
  await expect(paper.getByLabel(t("vehicles.owner_first_name"))).toHaveValue("Somchai");
  await expect(paper.getByLabel(t("vehicles.owner_address"))).toHaveValue("Phuket, 1 Beach Rd");
  await paper.getByRole("radio", { name: "Тест Аренда", exact: true }).check();
  // A value read wrong is cleared or corrected; the rest is written with the card.
  await paper.getByLabel(t("vehicles.seats"), { exact: true }).fill("");
  await paper.getByLabel(t("vehicles.power_kw")).fill("64.5");
  // The paper and the vehicle passport it names are offered as documents of the card; the
  // passport is not wanted here.
  const offered = paper.locator(".paper-document");
  await expect(offered).toHaveCount(2);
  await expect(offered.nth(0).getByLabel(t("vehicles.document_number"))).toHaveValue("RB-778");
  await expect(offered.nth(0)).toContainText(t("vehicles.paper_document_file"));
  await expect(offered.nth(1).getByLabel(t("vehicles.document_number"))).toHaveValue("PS-42");
  await offered.nth(1).getByRole("checkbox").uncheck();
  const made = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/vehicles"));
  await paper.getByRole("textbox", { name: new RegExp(`^${t("vehicles.name")}`) }).first().fill("Toyota Yaris");
  await paper.getByLabel(t("vehicles.location")).selectOption({ label: "Пхукет" });
  await page.getByRole("button", { name: t("vehicles.create") }).click();
  const sent = (await made).postDataJSON() as Record<string, unknown>;
  expect(sent.color).toBe("White");
  expect(sent.engine_volume_cc).toBe(1197);
  expect(sent.seats).toBeUndefined();
  expect(sent.power_kw).toBe("64.5");
  expect(sent.vehicle_category).toBe("B");
  // The province of the plate is kept apart from it and shown in the card.
  expect(sent.plate_region).toBe("Phuket");
  expect((sent.documents as { name: string; number: string }[]).map((d) => [d.name, d.number])).toEqual([
    ["Registration book", "RB-778"],
  ]);
  // The document is in the card with the photo of the paper.
  await expect(page.locator(".document-summary")).toContainText("Registration book");
  await expect(page.locator("dl.facts", { hasText: t("vehicles.plate_region") })).toContainText("Phuket");
  await expect(page.getByRole("heading", { name: "Toyota Yaris" })).toBeVisible();
  // The region is typed by hand in the edit form, beside the plate and its country.
  // «Change» is a pencil everywhere (customer's remark of 24.11.2026).
  // The tab «Main» has no second title «Main» inside it.
  await expect(page.getByRole("heading", { name: t("vehicles.card_main"), exact: true })).toHaveCount(0);
  await page.locator("section.card").first().getByRole("button", { name: t("common.edit"), exact: true }).click();
  // The form has the head of the card: «Back» and the title in one line.
  await expect(page.getByRole("heading", { name: t("vehicles.edit_title"), exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: t("common.back"), exact: true })).toBeVisible();
  // One form without named blocks; a make out of the catalog stands in the field of the make.
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.section_identification"));
  await expect(page.locator('input[list="vehicle-makes"]')).toHaveValue("Toyotaa");
  await page.getByLabel(t("vehicles.plate_region"), { exact: true }).fill("Krabi");
  // The bodies are those of the type: a passenger car has no van (it is a body of a truck).
  const bodies = page.locator('select:has(option[value="sedan"])');
  await expect(bodies.locator("option")).toHaveCount(11);
  await expect(bodies.locator('option[value="van"]')).toHaveCount(0);
  // The owner is named shortly, without the facts of the company.
  await expect(page.getByLabel(t("vehicles.owner_short_managed"), { exact: true })).toBeVisible();
  await expect(page.getByLabel(t("vehicles.owner_short_client"), { exact: true })).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText(t("companies.company_id"));
  await page.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(page.locator("dl.facts", { hasText: t("vehicles.plate_region") })).toContainText("Krabi");
  // The same paper again: the company already keeps this vehicle, so no second card is made —
  // the earlier one is named with a link to it (customer's decision of 23.11.2026).
  const yaris = page.url();
  await page.goto(`${base}/vehicles/new?paper=0190a000-0000-7000-8000-000000000000`);
  await paper.getByRole("textbox", { name: new RegExp(`^${t("vehicles.name")}`) }).first().fill("Yaris again");
  await paper.getByLabel(t("vehicles.location")).selectOption({ label: "Пхукет" });
  await page.getByRole("button", { name: t("vehicles.create") }).click();
  await expect(page.getByTestId("already-here")).toContainText("Toyota Yaris");
  await page.getByTestId("already-here").getByRole("link").click();
  await expect(page).toHaveURL(yaris);

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
  // The client is created step by step: kind, name, birth date is skipped, contacts — no step of
  // an ID; a contact is added by its kind first, a messenger is a contact of its own (customer's
  // remarks of 25.11.2026).
  await page.getByRole("link", { name: t("clients.new_button") }).click();
  const further = page.getByRole("button", { name: t("wizard.next"), exact: true });
  const skip = page.getByRole("button", { name: t("wizard.skip"), exact: true });
  await further.click();
  await page.getByLabel(t("profile.first_name"), { exact: true }).fill("Иван");
  await page.getByLabel(t("profile.last_name"), { exact: true }).fill("Петров");
  await further.click();
  await skip.click();
  const contacts = page.getByTestId("contacts-editor");
  // The kind of a contact is chosen first from «Choose»; a phone takes only digits (25.11.2026).
  await expect(contacts.getByLabel(t("clients.contact_kind"), { exact: true }).first()).toHaveValue("");
  await contacts.locator("select").first().selectOption("phone");
  await contacts.getByLabel(t("clients.contact_value_phone")).fill("+66 81 234 5678");
  await expect(contacts.getByLabel(t("clients.contact_value_phone"))).toHaveValue("+66812345678");
  await contacts.getByRole("button", { name: t("clients.contact_add") }).click();
  await contacts.locator("select").nth(1).selectOption("telegram");
  await contacts.getByLabel(t("clients.contact_value_messenger")).fill("@ivan_petrov");
  await page.getByRole("button", { name: t("clients.create") }).click();
  await expect(page.getByRole("main")).toContainText("@ivan_petrov");
  await expect(page.getByRole("main")).toContainText(t("clients.messenger_telegram"));
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
  await page.getByRole("tab", { name: t("vehicles.card_ownership") }).click();
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
  // The settings are split into tabs under the tab; the documents are one of them (customer's
  // decision of 24.11.2026).
  await page.getByRole("tab", { name: t("vehicles.settings_documents"), exact: true }).click();
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
  await page.goto(`${cardUrl}?tab=finance`);
  await page.getByRole("button", { name: `${t("common.edit")}: ${t("vehicles.acquisition_cost")}` }).click();
  await page.getByRole("dialog").getByLabel(t("vehicles.acquisition_cost")).fill("500000");
  await page.getByRole("dialog").getByLabel(t("vehicles.acquisition_currency")).selectOption("THB");
  await page.getByRole("dialog").getByRole("button", { name: t("common.save") }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // The cost is a line of the total, not a block of its own (customer's remark of 24.11.2026).
  await expect(page.getByRole("heading", { name: t("vehicles.acquisition"), exact: true })).toHaveCount(0);
  await expect(card(page, t("vehicles.finance_result"))).toContainText(t("vehicles.acquisition"));
  await expect(card(page, t("vehicles.finance_result"))).toContainText("500");
  await expect(card(page, t("vehicles.finance_result")).locator("input")).toHaveCount(0);
  // Incomes and expenses go in one list.
  await expect(card(page, t("vehicles.finance_moves"))).toBeVisible();
  // The total stands at the right of the list, at its top.
  const listBox = await page.locator(".finance-list").boundingBox();
  const sideBox = await page.locator(".finance-side").boundingBox();
  expect(sideBox!.x).toBeGreaterThan(listBox!.x + listBox!.width - 1);
  expect(Math.abs(sideBox!.y - listBox!.y)).toBeLessThan(4);
  // The plates stand under the owners, not as a list of their own (customer's remark of
  // 24.11.2026).
  await page.goto(`${cardUrl}?tab=ownership`);
  await expect(page.getByRole("heading", { name: t("vehicles.owners_title"), exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: t("vehicles.plates_title"), exact: true })).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText("AB 1234 TH");
  // Who added the document is written in the journal of the card.
  // The journal is a part of the settings; the old address of the tab «History» leads to it.
  await page.goto(`${cardUrl}?tab=history`);
  await expect(page.getByRole("tab", { name: t("vehicles.settings_history"), exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(card(page, t("vehicles.journal_title"))).toContainText(t("vehicles.journal_actions.vehicle_document-added"));
  await page.goto(cardUrl);
  await expect(page.locator("section.card").first()).toContainText("Blue Book");
  await expect(page.locator("section.card").first()).toContainText("123456");

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
  // Who issued the licence, as for the other documents (customer's remark of 25.11.2026).
  await editing.getByLabel(t("cards.issued_by"), { exact: true }).fill("ГИБДД 7701");
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
  await expect(documents).toContainText(`${t("cards.issued_by")}: ГИБДД 7701`);
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
  // The link is asked from the card: by a contact confirmed in a profile, by a letter to an email,
  // or by an ID (customer's decisions of 25.11.2026). No contact of this card is confirmed and it
  // has no email, so only the ID is left.
  const linking = card(page, t("links.title"));
  await linking.getByRole("button", { name: t("links.start") }).click();
  await expect(linking).toContainText(t("links.none_confirmed_no_email"));
  // The phone and the Telegram nickname are offered, but cannot be chosen.
  const disabled = linking.locator("option[disabled]");
  await expect(disabled).toHaveCount(2);
  await expect(disabled.filter({ hasText: `${t("clients.phone")}: +66812345678` })).toHaveCount(1);
  await expect(disabled.filter({ hasText: `${t("clients.messenger_telegram")}: @ivan_petrov` })).toHaveCount(1);
  await expect(linking.getByLabel(t("links.send_to"))).toHaveValue("id");
  await expect(linking.getByLabel(t("links.target_id"))).toBeVisible();
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
