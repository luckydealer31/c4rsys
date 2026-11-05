import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

/** What axe-core finds against WCAG 2.1 A and AA on the page (audit item 44): `rule: elements`. */
export async function violations(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 5).join(", ")}${v.nodes.length > 5 ? " …" : ""}`,
  );
}
