import { expect, type Page } from "@playwright/test";

import { bangkokIso, ok, type Company } from "./api";
import { t } from "./i18n";

// Helpers of the phone scenarios (audit items 41 and 45).

/** A 1×1 PNG: the photo of a position. */
export const PHOTO = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

/** Toyota Yaris issued to Иван Петров yesterday at 10:00; the rental id. */
export async function issueRental(page: Page, s: Company): Promise<string> {
  const issued = await ok(page, "POST", `/c/${s.cmp}/rentals/issue`, {
    vehicle_card_id: s.vehicle,
    client_id: s.client,
    tariff_id: s.tariff,
    issue_location_id: s.location,
    actual_start: bangkokIso(-1, "10:00"),
    odometer: "10000",
    fuel: "8",
  });
  return (issued.rental as { id: string }).id;
}

/** The inspection at issue started from the rental card; its id. */
export async function startIssueInspection(page: Page, s: Company, rental: string): Promise<string> {
  await page.goto(`${s.base}/rentals/${rental}`);
  await page.getByRole("link", { name: t("inspections.start_issue") }).click();
  await expect(page).toHaveURL(/\/inspections\/new\?/);
  await page.getByRole("button", { name: t("inspections.create") }).click();
  await expect(page).toHaveURL(/\/inspections\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(t("inspections.kind_issue"));
  return new URL(page.url()).pathname.split("/").pop() ?? "";
}

/** How far the page scrolls sideways, in CSS pixels. */
export function sideScroll(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/** What sticks out of the screen on the right: the innermost such elements, `tag.class (right)`. */
export function wideElements(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const limit = window.innerWidth + 1;
    const all = Array.from(document.body.querySelectorAll<HTMLElement>("*"));
    // What a scrolling block holds is scrolled inside it, not with the page.
    const clipped = (e: HTMLElement) => {
      for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
        if (getComputedStyle(p).overflowX !== "visible") return true;
      }
      return false;
    };
    const wide = all.filter((e) => e.getBoundingClientRect().right > limit && !clipped(e));
    return wide
      .filter((e) => !wide.some((other) => other !== e && e.contains(other)))
      .slice(0, 10)
      .map((e) => `${e.tagName.toLowerCase()}.${e.className} "${(e.textContent ?? "").trim().slice(0, 30)}" (${Math.round(e.getBoundingClientRect().right)})`);
  });
}

/** The visible controls lower than 44 px (§ 10.4.1, WCAG 2.5.5): `text (height)` of each. Text
 * links inside a sentence are not controls here; check boxes and radio buttons are taken by
 * their label. */
export function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const selector = [
      "button",
      ".button",
      "select",
      "textarea",
      "input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file])",
      ".rail-item",
      ".nav a",
      ".chip",
      ".topbar .bell",
    ].join(",");
    const found: string[] = [];
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (box.width === 0 || box.height === 0 || style.visibility === "hidden") continue;
      if (box.height < 43.5) {
        const name = (element.getAttribute("aria-label") ?? element.textContent ?? element.tagName).trim().slice(0, 40);
        found.push(`${name || element.tagName} (${Math.round(box.height)})`);
      }
    }
    return found;
  });
}
