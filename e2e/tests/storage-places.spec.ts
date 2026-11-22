import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";

// Customer's decision of 22.11.2026 (§ 11.1.9): the storage places are a list of each warehouse,
// kept in its settings as the posts of a service are. The stock shows the places and is searched
// by them; a place is chosen from the list, and a unit lies in one place.
test("storage places are a list of the warehouse", async ({ page }) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("places"), "Анна", "Петрова");
  const s = await createCompany(page);
  await ok(page, "POST", `/c/${s.cmp}/modules/warehouse/connect`, { locations: [s.location] });
  const locations = (await ok(page, "GET", `/c/${s.cmp}/locations`)).locations as {
    contexts: { id: string; type: string }[];
  }[];
  const context = locations.flatMap((l) => l.contexts).find((c) => c.type === "warehouse")!.id;
  const item = (await ok(page, "POST", `/c/${s.cmp}/warehouse/items`, { name: "Колодки", unit: "piece" })).item as {
    id: string;
  };
  // The places are a list of the warehouse (customer's decision of 22.11.2026): one is made
  // here, another in the settings of the warehouse below.
  await ok(page, "POST", `/c/${s.cmp}/warehouse/places`, { context_id: context, name: "Стеллаж 1" });
  const receipt = (
    await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts`, {
      source: "purchase",
      context_id: context,
      lines: [{ item_id: item.id, quantity: 1, unit_cost: "100", tags: ["Стеллаж 1"] }],
    })
  ).receipt as { id: string; version: number };
  await ok(page, "POST", `/c/${s.cmp}/warehouse/receipts/${receipt.id}/post`, { expected_version: receipt.version });

  // The settings of the warehouse keep the list of its places: a place is added by Enter, and
  // renamed with the pencil.
  await page.goto(`${s.base}/warehouse?tab=storages`);
  const placesList = page.locator(".places-of", { hasText: "Пхукет" });
  await expect(placesList.locator(".place-list li")).toHaveText([/Стеллаж 1/]);
  await placesList.getByRole("textbox", { name: t("places.new"), exact: true }).fill("Стеллаж 2");
  await placesList.getByRole("textbox", { name: t("places.new"), exact: true }).press("Enter");
  await expect(placesList.locator(".place-list li")).toHaveText([/Стеллаж 1/, /Стеллаж 2/]);
  await placesList.getByRole("button", { name: `${t("common.edit")}: Стеллаж 2`, exact: true }).click();
  await expect(placesList.getByRole("button", { name: t("warehouse.to_archive"), exact: true })).toBeVisible();
  await placesList.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  // The tags have no group of the places any more.
  await page.goto(`${s.base}/company/tags`);
  await expect(page.locator("section.tag-group")).toHaveCount(0);

  // The stock shows the place and is searched by it.
  await page.goto(`${s.base}/warehouse`);
  const rows = page.locator("table tbody tr");
  await expect(rows).toHaveCount(1);
  // The place tags are among the columns the arrow of the heading opens (customer's decision
  // of 20.11.2026).
  await page.getByRole("button", { name: t("stock.more_columns"), exact: true }).click();
  await expect(rows).toContainText("Стеллаж 1");
  await page.getByLabel(t("warehouse.search")).fill("стеллаж 1");
  await expect(rows).toHaveCount(1);
  await page.getByLabel(t("warehouse.search")).fill("стеллаж 9");
  await expect(page.getByText(t("stock.empty"))).toBeVisible();

  // The places of a unit are chosen, not written: a new one is typed by whoever may make it.
  await page.goto(`${s.base}/warehouse/items/${item.id}`);
  // The form of the item: no note of the category, and applicability asks for the type of the
  // vehicle before the make.
  await expect(page.getByText(t("warehouse.category"), { exact: true })).toHaveCount(0);
  // The explanation of the category is under «i», and an empty comment takes one line as high
  // as the field beside it (customer's remarks of 21.11.2026).
  await expect(page.getByText(t("categories.hint"))).toBeHidden();
  const comment = page.locator("textarea.growing");
  const low = (await comment.boundingBox())!.height;
  expect(low).toBeLessThan(50);
  await comment.fill("one\ntwo\nthree\nfour");
  expect((await comment.boundingBox())!.height).toBeGreaterThan(low + 40);
  await comment.fill("");
  expect((await comment.boundingBox())!.height).toBe(low);
  // The photo (customer's decisions of 21.11.2026): a file only, no link; without a photo there
  // is one button. It stands under the line of the barcodes and the comment.
  const card = page.locator("form").filter({ has: page.locator(".item-columns") });
  const pencil = card.locator(".item-photo").getByRole("button", { name: t("common.edit"), exact: true });
  const choose = card.getByRole("button", { name: t("images.choose"), exact: true });
  await expect(choose).toBeVisible();
  await expect(pencil).toHaveCount(0);
  await expect(card.locator(".item-photo").getByRole("textbox")).toHaveCount(0);
  const code = card.getByRole("textbox", { name: t("warehouse.barcode"), exact: true });
  expect(Math.abs((await code.boundingBox())!.y - (await comment.boundingBox())!.y)).toBeLessThan(40);
  expect((await choose.boundingBox())!.y).toBeGreaterThan((await code.boundingBox())!.y);
  await comment.fill("Проверить");
  await Promise.all([
    page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes(`/warehouse/items/${item.id}`)),
    card.getByRole("button", { name: t("common.save"), exact: true }).click(),
  ]);
  // What was saved is seen (customer's remark of 21.11.2026): «Saved» beside the button, and the
  // button waits for the next change. The way back names the catalog, not the whole section.
  const saving = card.getByRole("button", { name: t("common.save"), exact: true });
  await expect(card.getByRole("status")).toContainText(t("common.saved"));
  await expect(saving).toBeDisabled();
  await expect(page.getByRole("link", { name: `← ${t("warehouse.tab_items")}` })).toBeVisible();
  await comment.fill("Проверить ещё");
  await expect(saving).toBeEnabled();
  await expect(card.getByRole("status")).toHaveCount(0);
  // The blocks under the card (customer's remarks of 21.11.2026). The stock and the minimum are
  // one block: a line per warehouse.
  const place = (name: string) => page.locator(".place-line", { hasText: name });
  await expect(place("Пхукет")).toContainText("1");
  await expect(place("Пхукет").getByRole("textbox", { name: t("prices.minimum_title") })).toBeVisible();
  await expect(page.getByRole("heading", { name: t("prices.minimum_title") })).toHaveCount(0);
  // The barcodes are in the card itself: Enter adds one and does not save the card.
  await card.getByRole("textbox", { name: t("warehouse.barcode"), exact: true }).fill("4601234567890");
  await card.getByRole("textbox", { name: t("warehouse.barcode"), exact: true }).press("Enter");
  await expect(page.locator(".code-chip")).toContainText("4601234567890");
  // One button of deletion: a used item is offered the archive instead.
  const state = page.locator(".item-state");
  await state.getByRole("button", { name: t("locations.delete"), exact: true }).click();
  await state.getByRole("button", { name: t("locations.delete_confirm"), exact: true }).click();
  await expect(state.getByRole("alert")).toContainText(t("warehouse.delete_used"));
  await expect(state.getByRole("button", { name: t("warehouse.to_archive"), exact: true })).toBeVisible();
  await state.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  const adding = page
    .locator("section.card")
    .filter({ hasText: t("warehouse.section_parts") })
    .getByRole("button", { name: t("warehouse.add_applicability"), exact: true });
  await adding.click();
  const make = page.getByRole("combobox", { name: t("vehicles.make"), exact: true });
  await expect(make).toBeDisabled();
  await page.getByRole("combobox", { name: t("warehouse.vehicle_type") }).selectOption("passenger_car");
  await expect(make).toBeEnabled();
  // OK adds the row as text, the cross drops the one being added.
  await make.selectOption("TOYOTA");
  await page.getByRole("button", { name: t("calendar.ok"), exact: true }).click();
  await expect(page.locator(".applies-row")).toHaveText(/Toyota/);
  await adding.click();
  await page.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  await expect(make).toHaveCount(0);
  await expect(page.locator(".applies-row")).toHaveCount(1);
  // A country the list does not have is typed by hand.
  await page.locator('select:has(option[value="TH"])').selectOption("__other");
  await page.getByLabel(t("warehouse.country_other"), { exact: true }).fill("Абхазия");
  const patched = page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().includes("/warehouse/items/"));
  await page.getByRole("button", { name: t("common.save"), exact: true }).click();
  expect((await patched).status()).toBe(200);
  await page.reload();
  await expect(page.getByLabel(t("warehouse.country_other"), { exact: true })).toHaveValue("Абхазия");
  await expect(page.locator(".applies-row")).toHaveText(/Toyota/);
  // A make and a model named in the name are offered; pressing the offer adds them.
  await page.locator("label.field").filter({ hasText: new RegExp(`^${t("warehouse.name")}$`) }).locator("input").fill("Колодки передние ford focus");
  // The position named in the name presses its button.
  await expect(page.getByRole("button", { name: new RegExp(t("warehouse.value_front")) })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: new RegExp(t("warehouse.value_rear")) })).toHaveAttribute("aria-pressed", "false");
  const offer = page.getByRole("button", { name: "Ford Focus", exact: true });
  await offer.click();
  await expect(page.locator(".applies-row")).toHaveCount(2);
  await expect(offer).toHaveCount(0);
  // The units, their place marks and the sale price are not in the card of the catalog. A
  // double press on the row of the stock opens the card of the item in the stock: the lists are
  // in windows of their own, the catalog is a link (customer's decisions of 21.11.2026).
  await expect(page.getByRole("button", { name: t("stock.edit_tags"), exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: t("prices.title") })).toHaveCount(0);
  await page.goto(`${s.base}/warehouse?tab=stock`);
  await page.locator("tr.opens").first().dblclick();
  await expect(page).toHaveURL(/\/warehouse\/stock\//);
  await expect(page.locator(".scroll-window")).toContainText("AST-");
  // A piece item has no block of prices: the price is in the line of each unit. What the goods
  // are is read above the list.
  await expect(page.getByRole("heading", { name: t("prices.title") })).toHaveCount(0);
  await expect(page.locator(".goods-facts")).toContainText("Абхазия");
  // Everything about the goods is read here: the compatibility and the barcodes too.
  await expect(page.locator(".goods-facts")).toContainText("Toyota");
  await expect(page.locator(".goods-facts")).toContainText("4601234567890");
  await expect(page.locator(".scroll-window tbody td").nth(1)).toHaveText("1");
  await expect(page.getByRole("link", { name: t("stock.to_card"), exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: `← ${t("warehouse.tab_stock")}` })).toBeVisible();
  // No unit has a serial number, so there is no such column; the sale price is in the line of
  // the unit, and the storage place is named so (customer's remarks of 21.11.2026).
  const unitsHead = page.locator(".scroll-window thead");
  await expect(unitsHead).not.toContainText(t("receipts.serial_number"));
  await expect(unitsHead).toContainText(t("stock.sale_price"));
  await expect(unitsHead).toContainText(t("stock.tags"));
  // Several units are marked and get one price at once (customer's decision of 21.11.2026).
  await page.getByRole("checkbox", { name: t("stock.mark_all"), exact: true }).check();
  const bar = page.locator(".units-bar");
  await expect(bar).toContainText(t("stock.marked", { count: 1 }));
  await expect(bar.getByRole("button", { name: t("stock.tags"), exact: true })).toBeVisible();
  await bar.getByRole("button", { name: t("stock.sale_price"), exact: true }).click();
  await bar.getByRole("textbox", { name: t("stock.sale_price"), exact: true }).fill("250");
  await bar.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(bar).toHaveCount(0);
  await expect(page.locator(".scroll-window tbody .price-input input")).toHaveValue("250.00");
  await page.getByRole("button", { name: t("stock.edit_tags"), exact: true }).first().click();
  // The place is chosen from the list of the warehouse, not typed; a unit has one place.
  const cell = page.locator("td", { has: page.locator(".place-select") }).last();
  await cell.getByRole("combobox", { name: t("stock.tags"), exact: true }).selectOption("Стеллаж 2");
  // The headings of the prices stand over their values (customer's remark of 22.11.2026).
  const middle = async (locator: ReturnType<typeof page.locator>) => {
    const box = (await locator.boundingBox())!;
    return box.x + box.width / 2;
  };
  const priceHead = page.locator(".units-table th.price-col");
  expect(Math.abs((await middle(priceHead)) - (await middle(page.locator(".units-table td.price-col .price-input"))))).toBeLessThan(12);
  // The button beside the list is small: the line does not grow and nothing moves aside.
  // The columns keep their places while a place is being chosen (customer's remark of
  // 22.11.2026): the price stands where it stood.
  const priceAt = async () => (await page.locator(".scroll-window tbody .price-input input").boundingBox())!.x;
  const editing = await priceAt();
  const saveIt = cell.getByRole("button", { name: t("common.save"), exact: true });
  expect((await saveIt.boundingBox())!.width).toBeLessThan(40);
  await saveIt.click();
  await expect(page.locator(".scroll-window tbody")).toContainText("Стеллаж 2");
  expect(Math.abs((await priceAt()) - editing)).toBeLessThan(2);
  await expect(page.locator(".scroll-window tbody")).not.toContainText("Стеллаж 1");
  // The marked units go into an operation of the warehouse (customer's remark of 22.11.2026):
  // it opens with the warehouse, the item and the units already chosen.
  const stockCard = page.url();
  await page.getByRole("checkbox", { name: t("stock.mark_all"), exact: true }).check();
  for (const kind of ["move", "write_off"]) {
    await expect(page.locator(".units-bar").getByRole("button", { name: t(`operations.new_${kind}`), exact: true })).toBeVisible();
  }
  // Goods are not issued «to a vehicle» from here: they leave into an order or into a service
  // (customer's decision of 22.11.2026). This location has no service, so only the order is
  // offered. The order is found by the phone; it shows the phone and the date.
  for (const kind of ["issue", "consume"]) {
    await expect(page.locator(".units-bar").getByRole("button", { name: t(`operations.new_${kind}`), exact: true })).toHaveCount(0);
  }
  await expect(page.locator(".units-bar").getByRole("button", { name: t("send.to_service"), exact: true })).toHaveCount(0);
  const buyer = ((await ok(page, "POST", `/c/${s.cmp}/clients`, {
    kind: "person",
    first_name: "Иван",
    last_name: "Покупатель",
    contacts: [{ kind: "phone", value: "+66812223344" }],
  })).client as { id: string }).id;
  const bought = ((await ok(page, "POST", `/c/${s.cmp}/warehouse/sales/start`, { client_id: buyer })).order as { id: string }).id;
  await page.locator(".units-bar").getByRole("button", { name: t("send.to_order"), exact: true }).click();
  const toOrder = page.getByRole("dialog", { name: t("send.to_order") });
  await toOrder.getByRole("textbox", { name: t("send.order_search") }).fill("2223344");
  await expect(toOrder.locator(".pick-list li").first()).toContainText("+66812223344");
  await toOrder.locator(".pick-list").first().getByRole("button").first().click();
  await expect(page).toHaveURL(new RegExp(`/orders/${bought}$`));
  await expect(page.locator("main")).toContainText("Колодки");
  // Before the order is closed the goods are simply taken out of it.
  const sold = ((await ok(page, "GET", `/c/${s.cmp}/warehouse/orders/${bought}/sale-lines`)).lines as { id: string }[])[0].id;
  await ok(page, "DELETE", `/c/${s.cmp}/warehouse/orders/${bought}/sale-lines/${sold}`);
  await page.goto(stockCard);
  await page.getByRole("checkbox", { name: t("stock.mark_all"), exact: true }).check();
  await page.locator(".units-bar").getByRole("button", { name: t("operations.new_write_off"), exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: t("operations.new_write_off") })).toBeVisible();
  await expect(page.locator(".line-card input[type=checkbox]")).toBeChecked();
  await page.goto(stockCard);

  // The section is laid out as the others are (customer's remarks of 20.11.2026): no heading
  // on the screen, the tabs at the left, the buttons of the open tab in a column at the right.
  await page.goto(`${s.base}/warehouse?tab=stock`);
  const side = page.locator(".side-actions");
  await expect(side.getByRole("link", { name: t("receipts.create") })).toBeVisible();
  await expect(side.getByRole("link", { name: t("sales.sell") })).toBeVisible();
  await expect(page.locator("main").getByRole("link", { name: t("receipts.create") })).toHaveCount(1);
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toHaveText(t("nav.warehouse"));
  expect((await heading.boundingBox())!.width).toBeLessThan(3);
  const first = (await page.getByRole("tab").first().boundingBox())!;
  expect(first.x).toBeLessThan(200);
  expect((await side.boundingBox())!.x).toBeGreaterThan(first.x + 600);
  await page.getByRole("tab", { name: t("warehouse.tab_operations"), exact: true }).click();
  await expect(side.getByRole("link", { name: t("operations.new_move") })).toBeVisible();
  await page.getByRole("tab", { name: t("warehouse.tab_items"), exact: true }).click();
  await expect(side.getByRole("link", { name: t("warehouse.create_item") })).toBeVisible();
  await page.getByRole("tab", { name: t("warehouse.tab_reserves"), exact: true }).click();
  await expect(side).toHaveCount(0);

  // The warehouses of the stock (customer's decisions of 20.11.2026): with two of them a list
  // with marks appears — all, one or several. With all it is one list with the column of the
  // warehouses; with some chosen the stock goes warehouse by warehouse, each under its name.
  const second = ((await ok(page, "POST", `/c/${s.cmp}/locations`, { name: "Чалонг" })).location as { id: string }).id;
  await ok(page, "POST", `/c/${s.cmp}/locations/${second}/contexts`, { type: "warehouse" });
  await page.goto(`${s.base}/warehouse?tab=stock`);
  const where = page.getByRole("button", { name: t("stock.warehouse"), exact: true });
  const choice = page.getByRole("dialog", { name: t("stock.warehouse") });
  const head = (key: string) => page.locator("th").filter({ hasText: new RegExp(`^${t(key)}$`) });
  const lines = page.locator("table tbody tr");
  // Two views (customer's decision of 20.11.2026): by items — one list with the column of the
  // warehouses and no choice of them; by warehouses — a block per warehouse.
  const byPlaces = page.getByRole("button", { name: t("stock.view_places"), exact: true });
  await expect(page.getByRole("button", { name: t("stock.view_items"), exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(where).toHaveCount(0);
  await expect(lines).toHaveCount(1);
  // By items the list of the items is searched while typing and marked.
  await ok(page, "POST", `/c/${s.cmp}/warehouse/items`, { name: "Фильтр", unit: "piece" });
  const which = page.getByRole("button", { name: t("stock.item"), exact: true });
  const list = page.getByRole("dialog", { name: t("stock.item") });
  await expect(which).toContainText(t("stock.all_items"));
  await which.click();
  // Typed on the keys, with no field: the list is narrowed, Backspace takes a letter back.
  // (Latin keys: the test types other letters as inserted text, without key presses.)
  await page.keyboard.type("zzzz");
  await expect(list.getByRole("checkbox")).toHaveCount(1);
  await page.keyboard.press("Backspace");
  await expect(list).toContainText("zzz");
  for (let i = 0; i < 3; i += 1) await page.keyboard.press("Backspace");
  await expect(list.getByRole("textbox")).toHaveCount(0);
  await expect(list.getByRole("checkbox")).toHaveCount(2);
  await page.keyboard.press("Escape");
  await expect(head("stock.tags")).toBeVisible();
  await expect(head("stock.warehouses")).toBeVisible();
  // The main columns stand first; the only unit is named by its identifier.
  await expect(lines.first()).toContainText("AST-");
  await expect(lines.first()).toContainText("Пхукет");
  await expect(head("warehouse.country")).toHaveCount(0);
  await expect(head("stock.category")).toBeVisible();
  // The order of the main columns (customer's remark of 22.11.2026): name, article, description,
  // category, storage place, stock, warehouses; the manufacturer, the country and the tags of
  // the item are among the rest.
  await expect(page.locator("table.stock-table thead th").nth(1)).toHaveText(t("warehouse.article"));
  await expect(page.locator("table.stock-table thead th").nth(2)).toHaveText(t("warehouse.model_characteristic"));
  await expect(page.locator("table.stock-table thead th").nth(4)).toHaveText(t("stock.tags"));
  // The arrow at the end of the heading opens the rest of the columns and closes them.
  await expect(head("stock.cost")).toHaveCount(0);
  await page.getByRole("button", { name: t("stock.more_columns"), exact: true }).click();
  await expect(head("stock.cost")).toBeVisible();
  await expect(lines.first()).toContainText("100 THB");
  await expect(head("warehouse.comment")).toBeVisible();
  await expect(head("warehouse.country")).toBeVisible();
  await expect(head("tags.title")).toBeVisible();
  await page.getByRole("button", { name: t("stock.less_columns"), exact: true }).click();
  await expect(head("stock.cost")).toHaveCount(0);
  // By warehouses every warehouse has its block and the column of the warehouses is gone.
  const block = (name: string) => page.getByRole("region", { name });
  await byPlaces.click();
  await expect(where).toContainText(t("stock.all_warehouses"));
  await expect(block("Пхукет").locator("tbody tr")).toHaveCount(1);
  await expect(block("Чалонг")).toContainText(t("stock.empty_here"));
  await expect(head("stock.warehouses")).toHaveCount(0);
  // The search stands at the right end of the line.
  const search = page.getByRole("textbox", { name: t("warehouse.search") });
  expect((await search.boundingBox())!.x).toBeGreaterThan((await where.boundingBox())!.x + 300);
  // Both warehouses chosen one by one are «all» again; one — its own block without the column.
  await where.click();
  await choice.getByLabel("Чалонг").check();
  await expect(where).toContainText("Чалонг");
  await expect(block("Чалонг")).toContainText(t("stock.empty_here"));
  await expect(block("Пхукет")).toHaveCount(0);
  await expect(head("stock.warehouses")).toHaveCount(0);
  await choice.getByLabel("Пхукет").click();
  await expect(where).toContainText(t("stock.all_warehouses"));
  await expect(choice.getByLabel(t("stock.all_warehouses"))).toBeChecked();
  await expect(block("Чалонг")).toBeVisible();
  await choice.getByLabel("Пхукет").click();
  await expect(block("Пхукет").locator("tbody tr")).toHaveCount(1);
  await expect(where).toContainText("Пхукет");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: t("warehouse.tab_storages"), exact: true }).click();
  await expect(page.getByRole("heading", { name: t("operations.settings_title") })).toBeVisible();
  await expect(page.locator("section.card")).toHaveCount(1);
  // A new supplier is made from the list of suppliers itself, in a window (customer's decision
  // of 20.11.2026); there is no separate button.
  await page.goto(`${s.base}/warehouse/receipts/new`);
  await page.locator('select:has(option[value="purchase"])').selectOption("purchase");
  await expect(page.getByRole("button", { name: t("receipts.new_supplier"), exact: true })).toHaveCount(0);
  await page.locator('select:has(option[value="__new"])').selectOption("__new");
  await expect(page.getByRole("dialog", { name: t("receipts.new_supplier_title") })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: t("receipts.new_supplier_title") })).toHaveCount(0);

  // A document of a supplier is read into offers (customer's decision of 20.11.2026). The outer
  // service is not chosen yet, so its answer is put here in place of the server's.
  await page.route("**/api/catalog/warehouse-options", async (route) => {
    const answer = await route.fetch();
    await route.fulfill({ response: answer, json: { ...(await answer.json()), invoice_recognition: true } });
  });
  await page.route("**/warehouse/receipts/recognize", (route) =>
    route.fulfill({
      json: {
        supplier_name: "Phuket Parts",
        document_date: "2026-11-12",
        document_number: "INV-77",
        currency: "THB",
        lines: [
          { name: "Brake pads", article: "BP-1", quantity: "2", unit_cost: "1250.50", item: { id: item.id, name: "Колодки", unit: "piece", accounting: "individual", article: "" }, candidates: [] },
          { name: "Oil filter W712", article: "W712", quantity: "4", unit_cost: "180", item: null, candidates: [] },
        ],
      },
    }),
  );
  await page.reload();
  const run = page.getByRole("button", { name: t("recognition.run"), exact: true });
  await expect(run).toHaveCount(0); // no document yet
  await page.locator('input[type="file"]').setInputFiles({
    name: "note.png",
    mimeType: "image/png",
    buffer: Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)]),
  });
  await run.click();
  const reading = page.locator("section.card").filter({ hasText: t("recognition.title") });
  await expect(reading).toContainText("Phuket Parts");
  // The supplier read is a counterparty: none alike here, so a new one starts with the name read
  // and, once made, is the supplier of the receipt.
  await reading.getByRole("button", { name: t("recognition.supplier_create"), exact: true }).click();
  const window = page.getByRole("dialog", { name: t("receipts.new_supplier_title") });
  await expect(window.locator('input[value="Phuket Parts"]')).toHaveCount(1);
  await window.locator('input[type="email"]').fill("parts@example.com");
  await window.locator('button[type="submit"]').click();
  await expect(window).toHaveCount(0);
  await expect(reading).toContainText(t("recognition.supplier_chosen", { name: "Phuket Parts" }));
  await expect(reading).toContainText("12.11.2026");
  await expect(reading.locator("tbody tr")).toHaveCount(2);
  // The found item is added by a press; the unknown one is made and added.
  await reading.getByRole("button", { name: t("recognition.add"), exact: true }).click();
  await reading.getByRole("button", { name: t("recognition.create"), exact: true }).click();
  await expect(reading.getByText(t("recognition.added"))).toHaveCount(2);
  await expect(page.locator('input[value="1250.50"]')).toHaveCount(1);
});
