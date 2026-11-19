import { expect, test, type Page } from "@playwright/test";

import { bangkok, bangkokIso, createCompany, ok, registerUser, uniqueEmail, waitForServer, type Company } from "../lib/api";
import { t } from "../lib/i18n";
import { issueRental, startIssueInspection } from "../lib/mobile";

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
  await registerUser(page, uniqueEmail("park"), "Анна", "Петрова");
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

  // The occupancy for a period (customer's decisions of 20.11.2026). The booking of Honda Civic
  // lasts from the day after tomorrow 10:00 for a day: inside it the vehicle is booked, after it
  // — free; the issue, which is of this moment, is not offered for a period; the booking begun
  // from the row takes the period with it.
  const field = (key: string, part: string) => page.getByLabel(`${t(`rentals.park_${key}_label`)}: ${t(`moment.${part}`)}`);
  const moment = async (key: string) => `${await field(key, "date").inputValue()}T${await field(key, "time").inputValue()}`;
  const set = async (key: string, value: string) => {
    await field(key, "date").fill(value.slice(0, 10));
    await field(key, "time").fill(value.slice(11));
  };
  await set("from", bangkok(2, "12:00"));
  await set("to", bangkok(2, "18:00"));
  await expect(row("Honda Civic")).toContainText(t("rentals.park_booked"));
  await expect(page.getByRole("link", { name: t("rentals.park_issue"), exact: true })).toHaveCount(0);
  await set("from", bangkok(5, "10:00"));
  await set("to", bangkok(6, "10:00"));
  await expect(row("Honda Civic")).toContainText(t("rentals.park_free"));
  await expect(row("Honda Civic")).not.toContainText(t("rentals.park_booked"));
  await expect(row("Honda Civic").getByRole("link", { name: t("rentals.park_book"), exact: true })).toHaveAttribute(
    "href",
    new RegExp(`from=${bangkok(5, "10:00")}&to=${bangkok(6, "10:00")}`),
  );

  // One button goes round the ready periods: now, today, tomorrow, 7 days, a month. A period
  // typed by hand is «own», and the next press is «now». The location works from 9:00, so
  // «tomorrow» begins at 9:00 and a date chosen in the calendar takes that time.
  const places = (await ok(page, "GET", `/c/${s.cmp}/locations`)) as { locations: { id: string; schedule: { version: number } }[] };
  const day = [{ from: "09:00", to: "18:00" }];
  await ok(page, "PUT", `/c/${s.cmp}/locations/${s.location}/schedule`, {
    weekly: Object.fromEntries(["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((name) => [name, day])),
    exceptions: [],
    expected_version: places.locations.find((l) => l.id === s.location)!.schedule.version,
  });
  await page.reload();
  const mode = (key: string) =>
    page.getByRole("button", { name: `${t("rentals.park_period")}: ${t(`rentals.park_mode_${key}`)}`, exact: true });
  await expect(mode("now")).toBeVisible();
  await expect(field("from", "date")).toHaveValue("");
  await mode("now").click();
  await expect(mode("today")).toBeVisible();
  expect(await moment("from")).toBe(bangkok(0, "09:00"));
  expect(await moment("to")).toBe(bangkok(1, "09:00"));
  await mode("today").click();
  expect(await moment("from")).toBe(bangkok(1, "09:00"));
  expect(await moment("to")).toBe(bangkok(2, "09:00"));
  await mode("tomorrow").click();
  // 7 days and a month are counted from this moment, not from the working day.
  expect((await moment("from")).slice(0, 10)).toBe(bangkok(0, "").slice(0, 10));
  expect((await moment("to")).slice(0, 10)).toBe(bangkok(7, "").slice(0, 10));
  expect((await moment("to")).slice(11)).toBe((await moment("from")).slice(11));
  await expect(row("Honda Civic")).toContainText(t("rentals.park_booked"));
  await mode("week").click();
  await expect(mode("month")).toBeVisible();
  expect((await moment("to")).slice(11)).toBe((await moment("from")).slice(11));
  await mode("month").click();
  await expect(mode("now")).toBeVisible();
  await expect(field("from", "date")).toHaveValue("");
  await expect(row("Honda Civic").getByRole("link", { name: t("rentals.park_issue"), exact: true })).toBeVisible();
  // A date chosen in the calendar takes the beginning of the working day.
  await field("from", "date").fill(bangkok(3, "").slice(0, 10));
  await expect(field("from", "time")).toHaveValue("09:00");
  await expect(mode("own")).toBeVisible();
  await mode("own").click();
  await expect(mode("now")).toBeVisible();

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
});
