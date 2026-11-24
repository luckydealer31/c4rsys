import { expect, test, type Page } from "@playwright/test";

import { bangkok, bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer, type Company } from "../lib/api";
import { t } from "../lib/i18n";
import { issueRental, startIssueInspection } from "../lib/mobile";
import { ownershipScenario } from "../lib/ownership";

// Customer's decisions of 17.11.2026. The park of the rental: the own and the managed vehicles,
// filtered by the occupancy, with what restricts a vehicle and with a booking and a rental begun
// from the row. The card of a vehicle shows the main angles of its last inspection — one large,
// the rest small under it — and the picture of the body type while there are no photos.

async function vehicle(page: Page, s: Company, name: string, body: string): Promise<{ id: string; version: number }> {
  const made = (
    await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
      name,
      location_id: s.location,
      odometer_unit: "km",
      vehicle_type: "passenger_car",
      body_type: body,
    })
  ).vehicle as { id: string; version: number };
  await ok(page, "PUT", `/c/${s.cmp}/tariffs/${s.tariff}/vehicles/${made.id}`, {
    rates: [{ from: 1, rate: "1000" }],
    mileage: { mode: "unlimited" },
    deposit: { required: false },
  });
  return made;
}

/** A picture drawn in the page: a photo of an angle that is told from the others. */
async function picture(page: Page, text: string, color: string): Promise<Buffer> {
  const data = await page.evaluate(
    ({ text, color }) => {
      const canvas = document.createElement("canvas");
      canvas.width = 800;
      canvas.height = 600;
      const c = canvas.getContext("2d")!;
      c.fillStyle = color;
      c.fillRect(0, 0, 800, 600);
      c.fillStyle = "#fff";
      c.font = "bold 64px sans-serif";
      c.textAlign = "center";
      c.fillText(text, 400, 320);
      return canvas.toDataURL("image/png").split(",")[1];
    },
    { text, color },
  );
  return Buffer.from(data, "base64");
}

test("the park is filtered by the occupancy and begins a booking; the card shows the photos", async ({ page }) => {
  await waitForServer(page);
  const email = uniqueEmail("park");
  await registerUser(page, email, "Анна", "Петрова");
  const s = await createCompany(page);
  const honda = await vehicle(page, s, "Honda Civic", "hatchback");
  const closed = await vehicle(page, s, "Mazda 3", "sedan");
  // The third vehicle is not admitted to the rental.
  await ok(page, "PATCH", `/c/${s.cmp}/vehicles/${closed.id}`, { contexts: [], expected_version: closed.version });
  // The first one is in a rental, the second one is booked.
  const rental = await issueRental(page, s);
  await ok(page, "POST", `/c/${s.cmp}/bookings`, {
    vehicle_card_id: honda.id,
    tariff_id: s.tariff,
    client_id: s.client,
    issue_location_id: s.location,
    planned_start: bangkokIso(2, "10:00"),
    planned_end: bangkokIso(3, "10:00"),
  });

  // The order of the tabs of the rental; the trips need their module.
  await page.goto(`${s.base}/rentals?tab=park`);
  await expect(page.getByRole("tab", { name: t("rentals.tab_park"), exact: true })).toBeVisible();
  const tabs = (await page.getByRole("tab").allTextContents()).filter((name) => name !== t("rentals.tab_trips"));
  expect(tabs).toEqual(
    ["rentals", "schedule", "services", "park", "catalog"].map((key) => t(`rentals.tab_${key}`)),
  );

  const rows = page.locator("table tbody tr");
  await expect(rows).toHaveCount(3);
  const row = (name: string) => rows.filter({ hasText: name });
  await expect(row("Toyota Yaris")).toContainText(t("rentals.park_rented"));
  await expect(row("Honda Civic")).toContainText(t("rentals.park_booked"));
  // A restricted vehicle is greyed and not said to be free; in place of the buttons of its row
  // stands the mark with the reason under the pointer.
  await expect(row("Mazda 3")).not.toContainText(t("rentals.park_free"));
  const mark = row("Mazda 3").getByRole("note");
  await expect(mark).toHaveText(t("rentals.park_limited"));
  await expect(mark).toHaveAttribute("data-tip", new RegExp(t("rentals.park_not_admitted")));
  await expect(row("Mazda 3")).toHaveClass(/limited/);
  await expect(row("Mazda 3").getByRole("link", { name: t("rentals.park_book"), exact: true })).toHaveCount(0);
  await expect(row("Honda Civic").getByRole("note")).toHaveCount(0);
  // A vehicle in a rental is booked for later, not issued.
  await expect(row("Toyota Yaris").getByRole("link", { name: t("rentals.park_book"), exact: true })).toBeVisible();
  await expect(row("Toyota Yaris").getByRole("link", { name: t("rentals.park_issue"), exact: true })).toHaveCount(0);
  await expect(row("Honda Civic").getByRole("link", { name: t("rentals.park_issue"), exact: true })).toBeVisible();

  const filter = (key: string) => page.getByRole("button", { name: t(`rentals.park_${key}`), exact: true });
  await filter("rented").click();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("Toyota Yaris");
  await filter("booked").click();
  await expect(rows).toContainText("Honda Civic");
  // Nothing is free: the third vehicle is restricted, and is found by that.
  await filter("free").click();
  await expect(rows).toHaveCount(0);
  await filter("limited").click();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("Mazda 3");
  await filter("all").click();
  await expect(rows).toHaveCount(3);

  // A restricted vehicle is greyed, but its card is still opened from its name.
  await row("Mazda 3").getByRole("link", { name: "Mazda 3", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/vehicles/${closed.id}$`));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Mazda 3");
  await page.goBack();
  await expect(rows).toHaveCount(3);

  // A booking begun from the row has its vehicle chosen.
  await row("Honda Civic").getByRole("link", { name: t("rentals.park_book"), exact: true }).click();
  await expect(page).toHaveURL(/\/rentals\/book\?vehicle=/);
  await expect(page.getByLabel(t("rentals.vehicle"))).toHaveValue(honda.id);

  // The card of a vehicle without photos: the picture of its type stands in their place.
  await page.goto(`${s.base}/vehicles/${honda.id}`);
  await expect(page.locator(".vehicle-photo-main.none")).toBeVisible();
  // The same picture as in the lists stands there (customer's remark of 23.11.2026).
  await expect(page.locator(".vehicle-photo-main.none img.vehicle-type-icon")).toBeVisible();

  // An inspection with the photos of two angles.
  const inspection = await startIssueInspection(page, s, rental);
  await page.getByLabel(t("inspections.odometer", { unit: "km" })).fill("10000");
  await page.getByLabel(t("inspections.fuel")).fill("8");
  const angles: [string, string][] = [
    ["front", "#2f6f4f"],
    ["rear", "#7a3b3b"],
  ];
  for (const [angle, color] of angles) {
    const name = t(`inspections.position_${angle}`);
    const position = page.locator(".position", { hasText: name }).first();
    await position.locator("input[type=file]:not([capture])").setInputFiles({
      name: `${angle}.png`,
      mimeType: "image/png",
      buffer: await picture(page, name, color),
    });
    await expect(position.locator(".position-head .badge")).toHaveText(/^1 \/ \d+$/);
  }
  await page.getByRole("radio", { name: t("inspections.conducted_yes") }).check();
  await page.getByRole("button", { name: t("inspections.complete") }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("inspections.state_saved"));

  // The front stands large; a small one pressed takes its place.
  await page.goto(`${s.base}/vehicles/${s.vehicle}`);
  const photos = page.locator(".vehicle-photos");
  await expect(photos.locator(".vehicle-photo-main img")).toHaveAttribute("alt", t("inspections.position_front"));
  await photos.getByRole("button", { name: t("inspections.position_rear"), exact: true }).click();
  await expect(photos.locator(".vehicle-photo-main img")).toHaveAttribute("alt", t("inspections.position_rear"));
  await expect(photos.getByRole("button", { name: t("inspections.position_front"), exact: true })).toBeVisible();
  await photos.getByRole("link", { name: new RegExp(`^${t("vehicles.photos_from", { when: "" })}`) }).click();
  await expect(page).toHaveURL(new RegExp(`/inspections/${inspection}$`));

  // The columns of every location stand in one vertical, whatever the rows hold (customer's
  // remark of 18.11.2026): a second location, with a vehicle of a long name and no marks.
  const other = ((await ok(page, "POST", `/c/${s.cmp}/locations`, { name: "Чалонг" })).location as { id: string }).id;
  await ok(page, "POST", `/c/${s.cmp}/locations/${other}/contexts`, { type: "rental" });
  await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
    name: "Mitsubishi Pajero Sport Elite Edition 2024",
    location_id: other,
    odometer_unit: "km",
  });
  for (const path of ["/rentals?tab=park", "/vehicles"]) {
    await page.goto(`${s.base}${path}`);
    await expect(page.locator("section.card table")).toHaveCount(2);
    const lefts = (index: number) =>
      page.locator("section.card table").nth(index).locator("thead th").evaluateAll((cells) => cells.map((c) => Math.round(c.getBoundingClientRect().left)));
    expect(await lefts(1)).toEqual(await lefts(0));
  }
  await page.goto(`${s.base}/rentals?tab=park`);
  await expect(rows).toHaveCount(4);
  // The park has no period (customer's decision of 20.11.2026): the occupancy is of now.
  await expect(page.getByRole("button", { name: t("period.choose"), exact: true })).toHaveCount(0);

  // The days of a list (customer's decisions of 20.11.2026): the calendar alone while everything
  // is shown; its window is our calendar with today chosen — «OK» is today, a date and «OK» is
  // one day, a date, another one and «OK» a period, «Cancel» leaves things as they are, the
  // small «Reset» takes the dates away; the chosen dates stand beside the calendar with the
  // arrows that turn them by their own length, and are remembered for the next visit. There
  // are no ready periods. The booking of Honda Civic begins in two days.
  await page.goto(`${s.base}/rentals?tab=rentals`);
  const list = page.locator("table tbody tr");
  await expect(list).toHaveCount(2);
  const booking = list.filter({ hasText: "Honda Civic" });
  const calendar = page.getByRole("button", { name: t("period.choose"), exact: true });
  const window = page.getByRole("dialog", { name: t("period.choose") });
  const arrow = (key: string) => page.getByRole("button", { name: t(`period.${key}`), exact: true });
  const date = (days: number) => bangkok(days, "").slice(0, 10);
  const press = async (scope: import("@playwright/test").Locator, day: string) => {
    const cell = scope.locator(`[data-date="${day}"]:not(.outside)`);
    if ((await cell.count()) === 0) await scope.getByRole("button", { name: t("calendar.next_month"), exact: true }).click();
    await cell.click();
  };
  const okay = (scope: import("@playwright/test").Locator) => scope.getByRole("button", { name: t("calendar.ok"), exact: true });
  await expect(calendar).toHaveAttribute("data-from", "");
  await expect(arrow("forward")).toHaveCount(0);
  // The search keeps its place whatever is chosen (customer's remark of 20.11.2026): the control
  // of the days stands right before it and grows to the left.
  const search = page.getByRole("search").last().locator("input");
  const searchAt = async () => Math.round((await search.boundingBox())!.x);
  const still = await searchAt();
  const gap = async () => Math.round((await search.boundingBox())!.x - ((await calendar.boundingBox())!.x + (await calendar.boundingBox())!.width));
  expect(await gap()).toBeLessThan(60);
  // The calendar opens with today chosen: «OK» at once is today, and the arrows turn it day
  // by day.
  await calendar.click();
  await expect(window.locator(`[data-date="${date(0)}"]`)).toHaveAttribute("aria-pressed", "true");
  await expect(window.getByRole("button")).toHaveCount((await window.locator(".calendar-day").count()) + 7);
  await okay(window).click();
  await expect(window).toHaveCount(0);
  await expect(calendar).toHaveAttribute("data-from", date(0));
  await expect(booking).toHaveCount(0);
  await arrow("forward").click();
  await expect(list).toHaveCount(0);
  await arrow("forward").click();
  await expect(calendar).toHaveAttribute("data-from", date(2));
  await expect(booking).toHaveCount(1);
  await arrow("back").click();
  await expect(calendar).toHaveAttribute("data-from", date(1));
  // The control keeps its width whatever the digits of the date (customer's remark of
  // 20.11.2026): thirty days turned one by one.
  const widths = new Set<number>();
  for (let i = 0; i < 30; i += 1) {
    widths.add(Math.round((await calendar.boundingBox())!.width));
    await arrow("forward").click();
  }
  expect([...widths].length, [...widths].join(",")).toBe(1);
  for (let i = 0; i < 30; i += 1) await arrow("back").click();
  await expect(calendar).toHaveAttribute("data-from", date(1));
  // One date in the calendar: the day after the booking begins — nothing starts on it.
  await calendar.click();
  await press(window, date(3));
  await okay(window).click();
  await expect(window).toHaveCount(0);
  await expect(list).toHaveCount(0);
  // A period: from tomorrow to the day the booking begins; the arrows turn it by two days.
  await calendar.click();
  await press(window, date(1));
  await press(window, date(2));
  await expect(window.locator(".calendar-day.marked")).toHaveCount(2);
  await okay(window).click();
  await expect(booking).toHaveCount(1);
  expect(await searchAt()).toBe(still);
  await arrow("forward").click();
  await expect(calendar).toHaveAttribute("data-from", date(3));
  await expect(calendar).toHaveAttribute("data-to", date(4));
  await expect(booking).toHaveCount(0);
  await arrow("back").click();
  // «Cancel» changes nothing, though the calendar offered today again.
  await calendar.click();
  await expect(window.locator(`[data-date="${date(0)}"]`)).toHaveAttribute("aria-pressed", "true");
  await press(window, date(5));
  await window.getByRole("button", { name: t("common.cancel"), exact: true }).click();
  await expect(window).toHaveCount(0);
  await expect(calendar).toHaveAttribute("data-from", date(1));
  // The choice is remembered: the next visit begins with it.
  await page.reload();
  await expect(calendar).toHaveAttribute("data-from", date(1));
  await expect(calendar).toHaveAttribute("data-to", date(2));
  await expect(booking).toHaveCount(1);
  // «Reset» takes the dates away: everything is shown again, and that is remembered too.
  await calendar.click();
  await window.getByRole("button", { name: t("calendar.reset"), exact: true }).click();
  await expect(calendar).toHaveAttribute("data-from", "");
  await expect(arrow("forward")).toHaveCount(0);
  await expect(list).toHaveCount(2);
  // The ordered services are filtered by the same control.
  await page.goto(`${s.base}/rentals?tab=services`);
  await expect(page.getByRole("button", { name: t("period.choose"), exact: true })).toBeVisible();

  // The schedule (customer's decisions of 20.11.2026): one calendar for every span — a day
  // shows the date whole, a week has it in the middle, a month is its calendar month; the arrows
  // turn a day, a week or a month; the chosen span pressed again brings today back.
  await page.goto(`${s.base}/rentals?tab=schedule`);
  const go = page.getByRole("button", { name: t("schedule.go_to"), exact: true });
  // The colours are explained beside the buttons of the section; the bar is one line and does
  // not repeat the dates the strip shows.
  await expect(page.locator(".side-actions").getByRole("note", { name: t("schedule.legend") })).toContainText(t("schedule.kind_booking"));
  await expect(page.locator(".schedule-bar ~ *")).toHaveCount(0);
  const span = (key: string) => page.getByRole("button", { name: t(`schedule.span_${key}`), exact: true });
  const turn = (key: string) => page.getByRole("button", { name: t(`schedule.${key}`), exact: true });
  await expect(span("week")).toHaveAttribute("aria-pressed", "true");
  const today = (await go.getAttribute("data-from"))!;
  const plus = (days: number, base = today) => {
    const moved = new Date(`${base}T12:00:00Z`);
    moved.setUTCDate(moved.getUTCDate() + days);
    return moved.toISOString().slice(0, 10);
  };
  await turn("forward").click();
  await expect(go).toHaveAttribute("data-from", plus(7));
  await span("week").click();
  await expect(go).toHaveAttribute("data-from", today);
  await span("day").click();
  await turn("forward").click();
  await expect(go).toHaveAttribute("data-from", plus(1));
  await turn("back").click();
  await turn("back").click();
  await expect(go).toHaveAttribute("data-from", plus(-1));
  await span("day").click();
  await expect(go).toHaveAttribute("data-from", today);
  await span("month").click();
  await turn("forward").click();
  const next = new Date(`${today}T12:00:00Z`);
  next.setUTCDate(1);
  next.setUTCMonth(next.getUTCMonth() + 1);
  await expect(go).toHaveAttribute("data-from", next.toISOString().slice(0, 10));
  await span("month").click();
  await expect(go).toHaveAttribute("data-from", today);

  // The span and the date stand side by side, in one line.
  const [spans, nav] = [(await span("day").boundingBox())!, (await go.boundingBox())!];
  expect(Math.abs(spans.y + spans.height / 2 - (nav.y + nav.height / 2))).toBeLessThan(8);
  // The locations are a list at the left of the same line — all, one or several; the last one
  // that is on stays on. The span and the date stand at the right.
  const places = page.getByRole("button", { name: t("schedule.location"), exact: true });
  const choice = page.getByRole("dialog", { name: t("schedule.location") });
  const left = (await places.boundingBox())!;
  expect(left.x).toBeLessThan(spans.x - 100);
  expect(Math.abs(left.y + left.height / 2 - (spans.y + spans.height / 2))).toBeLessThan(8);
  await expect(places).toContainText(t("schedule.all_locations"));
  // The kinds of vehicles are switched in the same bar; the strip takes the whole width of the
  // page, to the right edge of the buttons of the section.
  const kind = page.locator(".schedule-bar").getByRole("button", { name: t("vehicles.tab_own"), exact: true });
  await expect(kind).toHaveAttribute("aria-pressed", "true");
  // A joined pair without marks: pressed, a kind is hidden; the last one that is on stays on.
  const managed = page.locator(".schedule-bar").getByRole("button", { name: t("vehicles.tab_managed"), exact: true });
  await managed.click();
  await expect(managed).toHaveAttribute("aria-pressed", "false");
  await kind.click();
  await expect(kind).toHaveAttribute("aria-pressed", "true");
  await managed.click();
  await expect(managed).toHaveAttribute("aria-pressed", "true");
  const pair = (await page.getByRole("group", { name: t("schedule.kinds") }).boundingBox())!;
  expect(Math.abs(pair.height - left.height)).toBeLessThan(3);
  const wide = (await page.locator(".schedule-strip").boundingBox())!;
  const side = (await page.locator(".side-actions").boundingBox())!;
  expect(Math.abs(wide.x + wide.width - (side.x + side.width))).toBeLessThan(4);
  expect(wide.y).toBeGreaterThan(side.y + side.height);
  // One shape in the line: the span is as high as the date and the list of locations.
  const frame = (await page.getByRole("group", { name: t("schedule.span") }).boundingBox())!;
  expect(Math.abs(frame.height - nav.height)).toBeLessThan(3);
  expect(Math.abs(frame.height - left.height)).toBeLessThan(3);
  const pajero = page.getByRole("link", { name: /Mitsubishi Pajero/ });
  await expect(pajero).toBeVisible();
  await places.click();
  await choice.getByLabel("Чалонг").uncheck();
  await expect(pajero).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Honda Civic" })).toBeVisible();
  await expect(places).toContainText("Пхукет");
  await choice.getByLabel("Пхукет").click();
  await expect(choice.getByLabel("Пхукет")).toBeChecked();
  await choice.getByLabel(t("schedule.all_locations")).check();
  await expect(pajero).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(choice).toHaveCount(0);

  // The calendar of the schedule: one date is the day; two dates are a period of its own, of
  // any length — more than a month is scrolled sideways, and the arrows turn it by its length.
  const picker = page.getByRole("dialog", { name: t("schedule.go_to") });
  await go.click();
  await press(picker, plus(2));
  await okay(picker).click();
  await expect(picker).toHaveCount(0);
  await expect(go).toHaveAttribute("data-from", plus(2));
  await expect(span("month")).toHaveAttribute("aria-pressed", "true");
  await go.click();
  await press(picker, plus(2));
  // The double arrow turns a year, the single one a month: 80 days ahead is found by the months.
  const far = plus(80);
  for (let i = 0; i < 4 && (await picker.locator(`[data-date="${far}"]:not(.outside)`).count()) === 0; i += 1) {
    await picker.getByRole("button", { name: t("calendar.next_month"), exact: true }).click();
  }
  await picker.locator(`[data-date="${far}"]:not(.outside)`).click();
  await okay(picker).click();
  await expect(go).toHaveAttribute("data-from", plus(2));
  await expect(go).toHaveAttribute("data-to", far);
  for (const key of ["day", "week", "month"]) await expect(span(key)).toHaveAttribute("aria-pressed", "false");
  // The strip is wider than its frame and is scrolled.
  const strip = page.locator(".card", { has: page.getByRole("link", { name: "Honda Civic" }) }).last();
  await expect.poll(() => strip.evaluate((e) => e.scrollWidth > e.clientWidth + 100)).toBe(true);
  await turn("forward").click();
  await expect(go).toHaveAttribute("data-from", plus(1, far));
  // A span takes the period away and keeps the chosen day; pressed again, it brings today back.
  await span("week").click();
  await expect(span("week")).toHaveAttribute("aria-pressed", "true");
  await expect(go).toHaveAttribute("data-from", plus(2));
  await span("week").click();
  await expect(go).toHaveAttribute("data-from", today);

  // The trips (customer's decision of 20.11.2026): one list — all, mine or the free ones are a
  // choice over it; the finished ones are switched on apart; the buttons of the trips stand at
  // the right, with those of the other tabs.
  await ok(page, "POST", `/c/${s.cmp}/deliveries`, {
    vehicle_card_id: closed.id,
    to: { kind: "location", location_id: other },
    planned_start: bangkokIso(1, "10:00"),
    planned_end: bangkokIso(1, "12:00"),
  });
  await page.goto(`${s.base}/rentals?tab=trips`);
  const trips = page.locator("table tbody tr");
  const which = (key: string) => page.getByRole("button", { name: t(`trips.kind_${key}`), exact: true });
  await expect(which("all")).toHaveAttribute("aria-pressed", "true");
  await expect(trips).toHaveCount(1);
  await expect(page.locator("section.card")).toHaveCount(1);
  await expect(page.locator(".side-actions").getByRole("link", { name: t("trips.new") })).toBeVisible();
  await expect(page.locator(".side-actions").getByRole("link", { name: t("trips.catalog") })).toBeVisible();
  // Nobody drives it yet: it is free and not mine.
  await which("mine").click();
  await expect(trips).toHaveCount(0);
  await which("free").click();
  await expect(trips).toHaveCount(1);
  await expect(page.getByRole("switch", { name: t("trips.finished") })).toHaveCount(0);
  await which("all").click();
  await expect(page.getByRole("switch", { name: t("trips.finished") })).toBeVisible();
  // The inspections and the rentals of a vehicle: a list at the left, the chosen one at the
  // right, and no second title of the tab (customer's remark of 24.11.2026).
  await page.goto(`${s.base}/vehicles/${s.vehicle}?tab=inspections`);
  await expect(page.locator(".pick-list .pick-row").first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".pick-peek")).toContainText(t("inspections.damages"));
  await expect(page.getByRole("heading", { name: t("inspections.title"), exact: true })).toHaveCount(0);
  await page.goto(`${s.base}/vehicles/${s.vehicle}?tab=rentals`);
  await expect(page.locator(".pick-list .pick-row").first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".pick-peek").getByRole("link", { name: t("vehicles.peek_open_rental"), exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: t("vehicles.rentals"), exact: true })).toHaveCount(0);
  // The general settings: what the vehicle is used in with the readiness beside it, the
  // restriction and the odometer; the name is not repeated, the notes stand under the «i», and
  // the button names the unit it changes to (customer's remark of 24.11.2026).
  await page.goto(`${s.base}/vehicles/${honda.id}?tab=settings&part=general`);
  await expect(page.getByRole("heading", { name: new RegExp(`^${t("vehicles.restriction_title")}`) })).toBeVisible();
  await expect(page.getByRole("button", { name: t("vehicles.unit_change_to_mi"), exact: true })).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.replacement_where"));
  await expect(page.getByRole("main")).not.toContainText(t("vehicles.restriction_hint"));
  // The tariffs of a vehicle stand in its settings; what a tariff holds for this vehicle — its
  // prices, mileage and deposit — is changed there by the pencil (customer's remark of
  // 24.11.2026).
  await page.goto(`${s.base}/vehicles/${honda.id}?tab=settings&part=tariffs`);
  await expect(page.getByRole("tab", { name: t("vehicles.settings_tariffs"), exact: true })).toHaveAttribute("aria-selected", "true");
  const terms = page.locator("section.card", { has: page.locator("dl.facts") }).first();
  await expect(terms).toContainText("1000 THB");
  await expect(terms.locator("input")).toHaveCount(0);
  await terms.getByRole("button", { name: new RegExp(`^${t("common.edit")}: `) }).click();
  const editing = page.locator("section.card", { has: page.getByRole("button", { name: t("common.save"), exact: true }) });
  await editing.locator("input").first().fill("1500");
  await editing.getByLabel(new RegExp(`^${t("terms.deposit")}`)).fill("5000");
  await editing.getByRole("button", { name: t("common.save"), exact: true }).click();
  await expect(terms).toContainText("1500 THB");
  await expect(terms).toContainText("5000 THB");
  // The cards of one vehicle in the company: no doubles, the archive, the merge.
  await ownershipScenario(page, s, email);
});
