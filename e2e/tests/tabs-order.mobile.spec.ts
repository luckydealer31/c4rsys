import { expect, test, type Page } from "@playwright/test";

import { createCompany, registerUser, uniqueEmail, waitForServer } from "../lib/api";
import { t } from "../lib/i18n";
import { sideScroll } from "../lib/mobile";
import { doneButton, dragPoints, expectOrdering, orderButton, tabNames, tabOf } from "../lib/tabs";

// Customer's decision of 17.11.2026: the order of the tabs is changed on a phone too — in the
// same mode, by a finger. On the Android project the finger is a real touch of the browser
// (which also shows that the page is not scrolled instead); on the iPhone project, where a test
// has no such touch, the events of a touch pointer are sent to the page.

type Point = { x: number; y: number };

async function fingerDrag(page: Page, start: Point, end: Point, real: boolean): Promise<void> {
  const steps = 8;
  const at = (i: number): Point => ({
    x: start.x + ((end.x - start.x) * i) / steps,
    y: start.y + ((end.y - start.y) * i) / steps,
  });
  if (real) {
    const cdp = await page.context().newCDPSession(page);
    const touch = (type: string, points: Point[]) =>
      cdp.send("Input.dispatchTouchEvent", { type: type as "touchStart", touchPoints: points });
    await touch("touchStart", [start]);
    for (let i = 1; i <= steps; i += 1) await touch("touchMove", [at(i)]);
    // The finger stops before it is lifted, as when a thing is put in its place: lifted on the
    // move it is a throw, and the browser passes over a tap that follows a throw at once.
    for (let i = 0; i < 3; i += 1) {
      await page.waitForTimeout(80);
      await touch("touchMove", [end]);
    }
    await touch("touchEnd", []);
    await cdp.detach();
    return;
  }
  const send = (type: string, p: Point) =>
    page.evaluate(
      ({ type, p }) => {
        (document.elementFromPoint(p.x, p.y) ?? document.body).dispatchEvent(
          new PointerEvent(type, {
            bubbles: true,
            cancelable: true,
            pointerId: 71,
            pointerType: "touch",
            isPrimary: true,
            clientX: p.x,
            clientY: p.y,
          }),
        );
      },
      { type, p },
    );
  await send("pointerdown", start);
  for (let i = 1; i <= steps; i += 1) await send("pointermove", at(i));
  await send("pointerup", end);
}

test("the tabs are put in order by a finger on a phone", async ({ page }, info) => {
  await waitForServer(page);
  await registerUser(page, uniqueEmail("tabs-phone"), "Анна", "Петрова");
  const s = await createCompany(page);
  await page.goto(`${s.base}/rentals`);
  const park = t("rentals.tab_park");
  const first = t("rentals.tab_rentals");
  await expect(tabOf(page, park)).toBeVisible();
  expect((await tabNames(page))[0]).toBe(first);
  expect(await sideScroll(page)).toBeLessThanOrEqual(1);

  // The button of the order is large enough for a finger.
  const button = await orderButton(page).boundingBox();
  expect(Math.min(button?.width ?? 0, button?.height ?? 0)).toBeGreaterThanOrEqual(43.5);
  await orderButton(page).tap();
  await expectOrdering(page, true);

  // The park is carried to the first place: the tabs stand in a few lines on a narrow screen.
  const { start, end } = await dragPoints(page, park, first, "before");
  const scrolled = await page.evaluate(() => window.scrollY);
  await fingerDrag(page, start, end, info.project.name === "android");
  await expect.poll(async () => (await tabNames(page)).slice(0, 2)).toEqual([park, first]);
  // The tab the user is on stays while the order is changed.
  await expect(tabOf(page, first)).toHaveAttribute("aria-selected", "true");
  expect(await page.evaluate(() => window.scrollY)).toBe(scrolled);
  expect(await sideScroll(page)).toBeLessThanOrEqual(1);

  // Out of the mode a tap opens the tab; the order stays after the page is opened again.
  await doneButton(page).tap();
  await expectOrdering(page, false);
  await tabOf(page, park).tap();
  await expect(page).toHaveURL(/tab=park/);
  // The park fits a narrow screen: its filter stands in a few lines.
  await expect(page.getByRole("button", { name: t("rentals.park_limited"), exact: true })).toBeVisible();
  expect(await sideScroll(page)).toBeLessThanOrEqual(1);
  await page.reload();
  await expect(tabOf(page, park)).toBeVisible();
  await expect.poll(async () => (await tabNames(page)).slice(0, 2)).toEqual([park, first]);
});
