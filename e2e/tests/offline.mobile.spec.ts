import { expect, test } from "@playwright/test";

import { createCompany, ok, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";
import { issueRental, startIssueInspection } from "../lib/mobile";

// Audit item 45: the connection lost in the middle of an inspection on a phone (§ 10.4.2–10.4.3).
// The change stays on the device and is sent by itself when the connection returns. On the test
// server build (`C4R_BUILD=prod`) the service worker is there too: the page reloaded without
// connection opens the interface of local drafts, and the draft is in it.
const PROD = process.env.C4R_BUILD === "prod";

test("a change made without connection is sent when it returns", async ({ page, context, browserName }) => {
  // Playwright's offline mode in WebKit stops a navigation before the service worker sees it
  // (checked 05.11.2026: the worker controls the page, a reload still fails), so the reload
  // without connection is checked on Chromium only; the rest runs on both.
  const reloadOffline = PROD && browserName !== "webkit";
  if (PROD && !reloadOffline) {
    test.info().annotations.push({ type: "skipped step", description: "reload without connection (WebKit emulation)" });
  }
  await waitForServer(page);
  await registerUser(page, uniqueEmail("offline"), "Анна", "Петрова");
  const s = await createCompany(page);
  const rental = await issueRental(page, s);
  const inspection = await startIssueInspection(page, s, rental);
  const odometer = page.getByLabel(t("inspections.odometer", { unit: "km" }));
  const save = page.locator(".sticky-actions button");

  // With the connection: the draft is on the server.
  await odometer.fill("10000");
  await page.getByLabel(t("inspections.fuel")).fill("8");
  await save.click();
  await expect(save).toHaveText(t("inspections.draft_saved"));
  if (reloadOffline) {
    // The service worker has the shell of the build before the connection is lost.
    await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
    });
  }

  // The connection is lost: the change stays on this device.
  await context.setOffline(true);
  await odometer.fill("10050");
  await save.click();
  await expect(page.getByText(t("offline.edit_waiting"))).toBeVisible();

  if (reloadOffline) {
    // Reloaded without connection: the interface of local drafts of this company, with this
    // draft in it.
    await page.reload();
    await expect(page.getByText(t("offline.no_connection"), { exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: t("offline.edit_of_inspection") })).toBeVisible();
    // The connection is back: the application leaves the offline interface by itself.
    await context.setOffline(false);
    await expect(page.getByText(t("offline.no_connection"), { exact: true })).toHaveCount(0);
    // Opened again with the connection, the inspection sends the draft kept on the device.
    await page.goto(`${s.base}/inspections/${inspection}`);
  } else {
    await context.setOffline(false);
  }

  // The connection is back: the change reaches the server without another tap.
  await expect(page.getByText(t("offline.edit_waiting"))).toHaveCount(0);
  await expect(save).toHaveText(t("inspections.draft_saved"));
  await expect
    .poll(async () => ((await ok(page, "GET", `/c/${s.cmp}/inspections/${inspection}`)).inspection as { odometer: string }).odometer)
    .toMatch(/^10050(\.0+)?$/);
});
