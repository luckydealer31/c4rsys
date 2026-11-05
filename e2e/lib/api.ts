import { expect, type Page } from "@playwright/test";

import { mailCode } from "./docker";

// Preparation through the API (audit item 41): what a test does not check is created by the
// same requests the interface sends, from inside the page, so the cookies and the CSRF token
// work as in the application on both stacks (development and the test server build).

export type Body = Record<string, unknown>;

export async function waitForServer(page: Page): Promise<void> {
  await expect
    .poll(async () => (await page.request.get("/api/health").catch(() => null))?.status() ?? 0, {
      timeout: 120_000,
      intervals: [1000],
    })
    .toBe(200);
}

/** One request of the page: `[status, body]`. */
export async function call(page: Page, method: string, path: string, body?: Body): Promise<[number, Body]> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const cookie = () => document.cookie.match(/(?:^|; )csrftoken=([^;]*)/)?.[1];
      if (!cookie()) await fetch("/api/auth/csrf", { credentials: "same-origin" });
      const response = await fetch(`/api${path}`, {
        method,
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": cookie() ?? "",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
      });
      const text = await response.text();
      return [response.status, text ? (JSON.parse(text) as Record<string, unknown>) : {}] as [number, Record<string, unknown>];
    },
    { method, path, body },
  );
}

/** The request must succeed; its body. */
export async function ok(page: Page, method: string, path: string, body?: Body): Promise<Body> {
  const [status, answer] = await call(page, method, path, body);
  if (status >= 300) throw new Error(`${method} ${path}: ${status} ${JSON.stringify(answer)}`);
  return answer;
}

export function uniqueEmail(role: string): string {
  return `${role}+${Date.now()}${Math.floor(Math.random() * 1000)}@c4rsys.test`;
}

export function newPassword(): string {
  return `E2e-${Math.random().toString(36).slice(2)}-pass`;
}

/** Registers a named user in the page's browser context and leaves it signed in. */
export async function registerUser(page: Page, email: string, first: string, last: string): Promise<void> {
  await page.goto("/login");
  const started = await ok(page, "POST", "/auth/register/start", { email });
  const flow = started.flow as string;
  await ok(page, "POST", "/auth/register/verify", { flow, code: await mailCode(email) });
  await ok(page, "POST", "/auth/register/complete", { flow, password: newPassword() });
  await ok(page, "POST", "/me/personal-data", { first_name: first, last_name: last });
}

export type Company = {
  cmp: string;
  base: string;
  location: string;
  tariff: string;
  vehicle: string;
  client: string;
};

/** The company of the base example (STAGE1_ACCEPTANCE_TESTS): Asia/Bangkok, THB, location
 * «Пхукет» with the rental context, daily tariff 1 000 THB, Toyota Yaris with unlimited mileage
 * and no deposit, client Иван Петров. The signed-in user of the page is its director. */
export async function createCompany(page: Page): Promise<Company> {
  const company = (
    await ok(page, "POST", "/companies", {
      display_name: "Тест Аренда",
      timezone: "Asia/Bangkok",
      main_currency: "THB",
    })
  ).company as { public_id: string };
  const cmp = company.public_id;
  const location = ((await ok(page, "POST", `/c/${cmp}/locations`, { name: "Пхукет" })).location as { id: string }).id;
  await ok(page, "POST", `/c/${cmp}/locations/${location}/contexts`, { type: "rental" });
  const tariff = (
    (await ok(page, "POST", `/c/${cmp}/tariffs`, {
      name: "Сутки",
      ranges: [{ from: 1, to: null, rate: "1000" }],
    })).tariff as { id: string }
  ).id;
  const vehicle = (
    (await ok(page, "POST", `/c/${cmp}/vehicles`, {
      name: "Toyota Yaris",
      location_id: location,
      odometer_unit: "km",
    })).vehicle as { id: string }
  ).id;
  await ok(page, "PUT", `/c/${cmp}/tariffs/${tariff}/vehicles/${vehicle}`, {
    mileage: { mode: "unlimited" },
    deposit: { required: false },
  });
  const client = (
    (await ok(page, "POST", `/c/${cmp}/clients`, {
      kind: "person",
      first_name: "Иван",
      last_name: "Петров",
      contacts: [{ kind: "phone", value: "+66812345678" }],
    })).client as { id: string }
  ).id;
  return { cmp, base: `/c/${cmp}`, location, tariff, vehicle, client };
}

/** The date `days` from today in Asia/Bangkok, with the time: the value of a datetime-local field. */
export function bangkok(days: number, time: string): string {
  const date = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok" }).format(
    new Date(Date.now() + days * 24 * 60 * 60 * 1000),
  );
  return `${date}T${time}`;
}

/** The same moment as an ISO string with the Bangkok offset, for the API. */
export function bangkokIso(days: number, time: string): string {
  return `${bangkok(days, time)}:00+07:00`;
}
