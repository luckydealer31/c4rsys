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
    ignoreHTTPSErrors: true,
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
  await request.getByRole("checkbox", { name: new RegExp(`^${t("links.field_email")}:`) }).check();
  await request.getByRole("button", { name: t("links.accept") }).click();

  // The company: the notification of the answer leads straight to the link in the card, where
  // each given value stands beside what the card holds now (customer's remark of 25.11.2026).
  // (The notifications are raised by the schedule, not run here: the address it leads to.)
  await page.goto(`${s.base}/clients/${s.client}?tab=settings&part=access#profile-link`);
  const shared = page.getByTestId("shared-table");
  await expect(shared).toContainText(t("links.from_profile"));
  await expect(shared).toContainText(t("links.in_card"));
  // The email is not in the card yet; taken, it shows «In the card».
  const email = shared.getByRole("row").filter({ hasText: t("links.field_email") });
  await email.getByRole("button", { name: t("links.apply") }).click();
  await expect(email).toContainText(t("links.taken"));

  // The linked company and its history: the order with the rental of Toyota Yaris.
  await client.getByRole("link", { name: /Тест Аренда/ }).click();
  await expect(client).toHaveURL(/\/companies\/[0-9a-f-]+$/);
  // The fresh data of the profile are given by the person himself, from the page of the linked
  // company; the company does not ask for them (customer's decision of 26.11.2026).
  await client.getByTestId("give-data").click();
  const giving = client.getByTestId("give-data-form");
  await giving.getByRole("checkbox", { name: new RegExp(`^${t("links.field_email")}:`) }).check();
  await giving.getByRole("button", { name: t("data_requests.give") }).click();
  await expect(client.getByText(t("data_requests.given"))).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("given-data")).toContainText(t("data_requests.title"));
  await expect(page.getByTestId("given-data")).toContainText(t("links.field_email"));
  // The break is started by a button beside the state of the link; then a reason is chosen.
  await page.getByTestId("break-link").click();
  await expect(page.getByTestId("break-form").getByLabel(t("links.break_reason"))).toBeVisible();
  // A reason, a comment, then «Confirm»: without the comment the link cannot be broken; the
  // form warns that the link is not brought back by the company alone. «Cancel» leaves it.
  const breaking = page.getByTestId("break-form");
  await expect(breaking.getByRole("button", { name: t("links.break_yes") })).toBeDisabled();
  await expect(breaking.getByRole("button", { name: t("links.break") })).toHaveCount(0);
  await breaking.getByLabel(t("links.revoke_reason")).fill("Проверка");
  await expect(page.getByTestId("break-confirm")).toContainText(t("links.break_confirm"));
  await expect(breaking.getByRole("button", { name: t("links.break_yes") })).toBeEnabled();
  await breaking.getByRole("button", { name: t("common.cancel") }).click();
  await expect(page.getByTestId("break-link")).toBeVisible();
  await expect(client.getByText(t("cabinet.order", { number: 1 }))).toBeVisible();
  await expect(client.getByText(`${t("cabinet.op_rental")}: Toyota Yaris`)).toBeVisible();

  // The company asks the person as the owner of a managed vehicle. The request waits in
  // «Requests» with a number on the menu, not on the page of the companies; the questions come
  // one after another, and «no» to the ownership makes the management «no» (customer's
  // decisions of 25.11.2026).
  const managed = (
    await ok(page, "POST", `/c/${s.cmp}/vehicles`, {
      name: "Honda Forza",
      relation: "managed",
      // A vehicle in the management is made with its owner (customer's decision of 26.11.2026).
      owner_kind: "organization",
      owner_name: "Owner Co",
      location_id: s.location,
      odometer_unit: "km",
      vehicle_type: "motorcycle",
      make_other: "Honda",
      model_other: "Forza 350",
    })
  ).vehicle as { id: string };
  await ok(page, "POST", `/c/${s.cmp}/vehicles/${managed.id}/owner-requests`, { owner_lookup: clientEmail });
  await client.goto("/");
  await expect(client.locator(".rail-count")).toHaveText("1");
  await expect(client.getByText(t("owner_requests.my_title"))).toHaveCount(0);
  await client.goto("/requests");
  // The linked companies are not repeated here: only what waits for an answer.
  await expect(client.getByText(t("links.linked_companies"))).toHaveCount(0);
  const asking = client.locator("section.card", { hasText: "Honda Forza 350" });
  await asking.getByRole("button", { name: t("owner_requests.answer"), exact: true }).click();
  // The person is a linked client already: the link is not asked; each question is a window of
  // its own.
  const window = client.getByRole("dialog");
  await expect(window.getByText(t("owner_requests.step_ownership", { vehicle: "Honda Forza 350" }))).toBeVisible();
  await expect(window).not.toContainText(t("owner_requests.step_link", { company: "Тест Аренда" }));
  // «No» to the ownership: the management is «no» by itself, and the answers go at once.
  await window.getByRole("button", { name: t("owner_requests.no"), exact: true }).click();
  await expect(client.getByRole("dialog")).toHaveCount(0);
  const result = client.locator("section.card", { hasText: "Honda Forza 350" });
  await expect(result).toContainText(t("owner_requests.result_management"));
  await expect(result.getByRole("button", { name: t("owner_requests.answer"), exact: true })).toHaveCount(0);
  await expect(client.locator(".rail-count")).toHaveCount(0);
  // The company sees the request in one line with the answers in a row (customer's remark of
  // 26.11.2026).
  await page.goto(`${s.base}/vehicles/${managed.id}?tab=settings&part=access`);
  await expect(page.getByTestId("owner-request")).toHaveCount(1);
  await expect(page.getByTestId("owner-request")).toContainText(t("owner_requests.question_ownership"));
  const history = (await ok(page, "GET", `/c/${s.cmp}/vehicles/${managed.id}/owner-requests`)).requests as {
    state: string;
    answers: { question: string; answer: boolean }[];
  }[];
  expect(history[0]!.state).toBe("complete");
  expect(Object.fromEntries(history[0]!.answers.map((a) => [a.question, a.answer]))).toEqual({
    link: true,
    ownership: false,
    management: false,
  });

  // The company interface is not served on the cabinet domain.
  await client.goto(`${s.base}/rentals`);
  await expect(client.getByRole("heading", { level: 1 })).toHaveText(t("cabinet.companies"));
  await cabinet.close();
  // The link is broken with an explanation. A new request is started above, beside the state;
  // while it waits, the data given through the earlier link stay in the card (customer's
  // remarks of 26.11.2026).
  await page.goto(`${s.base}/clients/${s.client}?tab=settings&part=access`);
  await page.getByTestId("break-link").click();
  await page.getByTestId("break-form").getByLabel(t("links.revoke_reason")).fill("Клиент попросил");
  await page.getByTestId("break-form").getByRole("button", { name: t("links.break_yes") }).click();
  const linkCard = page.locator("#profile-link");
  await expect(linkCard).toContainText(t("links.state_broken"));
  await expect(linkCard).toContainText("Клиент попросил");
  await expect(page.getByTestId("shared-table").first()).toBeVisible();
  await page.getByTestId("start-link").click();
  await linkCard.getByRole("button", { name: t("links.send") }).click();
  await expect(linkCard).toContainText(t("links.state_pending"));
  await expect(page.getByTestId("earlier-link")).toContainText(t("links.earlier_title"));
  await expect(page.getByTestId("earlier-link")).toContainText("Клиент попросил");
});
