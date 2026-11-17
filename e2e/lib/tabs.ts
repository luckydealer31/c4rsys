import { expect, type Locator, type Page } from "@playwright/test";

import { t } from "./i18n";

// The order of the tabs of a section (customer's decisions of 17.11.2026): changed in its own
// mode, switched on by the button at the right end of the tabs.

export const tabNames = (page: Page): Promise<string[]> => page.getByRole("tab").allTextContents();

export const orderButton = (page: Page): Locator =>
  page.getByRole("button", { name: t("common.tabs_order"), exact: true });

export const doneButton = (page: Page): Locator =>
  page.getByRole("button", { name: t("common.tabs_order_done"), exact: true });

export const tabOf = (page: Page, name: string): Locator => page.getByRole("tab", { name, exact: true });

/** Where a tab is taken — its middle — and where it is put: the left or the right part of
 * another tab. */
export async function dragPoints(page: Page, from: string, to: string, side: "before" | "after") {
  const a = await tabOf(page, from).boundingBox();
  const b = await tabOf(page, to).boundingBox();
  if (!a || !b) throw new Error(`No tab ${from} or ${to}`);
  return {
    start: { x: a.x + a.width / 2, y: a.y + a.height / 2 },
    end: { x: side === "before" ? b.x + 4 : b.x + b.width - 4, y: b.y + b.height / 2 },
  };
}

/** A tab dragged by the mouse. */
export async function dragTab(page: Page, from: string, to: string, side: "before" | "after"): Promise<void> {
  const { start, end } = await dragPoints(page, from, to, side);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
}

/** The mode of the order is on: the tabs sway. */
export async function expectOrdering(page: Page, on: boolean): Promise<void> {
  const list = page.getByRole("tablist").first();
  if (on) await expect(list).toHaveClass(/ordering/);
  else await expect(list).not.toHaveClass(/ordering/);
}
