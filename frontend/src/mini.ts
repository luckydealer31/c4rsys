/** The short version of the site in the window of the Telegram bot (customer's decision of
 * 23.11.2026): only a vehicle, a delivery note and a receipt by a photo, the inspections, and —
 * since 28.11.2026 — a new client, investor or counterparty by a photo of a document; the
 * rest of the site — rental, service, finance, settings — is not opened from it. The mark lives
 * with the window: it is set when the window signs in and is gone when the window is closed.
 *
 * It is the look of the window, not a right: what the employee may do is checked by the server
 * as everywhere. */
const KEY = "c4r-mini";

export function enterMini(): void {
  try {
    window.sessionStorage.setItem(KEY, "1");
  } catch {
    // Without the storage the full site is shown.
  }
}

export function isMini(): boolean {
  try {
    return window.sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

// What of a company is opened in the window, after `/c/<company>`.
const PAGES = [
  /^\/app(\/(inspections|receipts))?$/,
  /^\/vehicles\/new$/,
  // A client, an investor and a counterparty by a photo of a document (customer's decision of
  // 28.11.2026).
  /^\/clients\/((investors|counterparties)\/)?new$/,
  /^\/warehouse\/receipts\/(new|[0-9a-f-]{36})$/,
  /^\/inspections\/(new|[0-9a-f-]{36}|drafts\/[^/]+)$/,
  /^\/damages\/[0-9a-f-]{36}$/,
];

/** Whether the window opens this page of a company; `rest` is the address after the company. */
export function miniPage(rest: string): boolean {
  return PAGES.some((page) => page.test(rest));
}
