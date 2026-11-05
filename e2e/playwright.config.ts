import { defineConfig, devices } from "@playwright/test";

// Projects (audit item 45): every scenario on a desktop Chromium; the phone scenarios
// (`*.mobile.spec.ts`) also on an Android phone (Chromium) and an iPhone (WebKit, the engine of
// every browser on iOS).
const MOBILE = /\.mobile\.spec\.ts$/;
// Chromium registers no service worker under a certificate it does not trust, whatever
// ignoreHTTPSErrors says; the flag is Chromium's own, WebKit takes no such argument.
const CHROMIUM = { launchOptions: { args: ["--ignore-certificate-errors"] } };

export default defineConfig({
  testDir: "./tests",
  timeout: 300_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:5173",
    // The test server build is checked over HTTPS with a certificate of Caddy's local authority.
    ignoreHTTPSErrors: true,
    locale: "ru-RU",
    timezoneId: "Asia/Bangkok",
    actionTimeout: 30_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], ...CHROMIUM }, testIgnore: MOBILE },
    { name: "android", use: { ...devices["Pixel 7"], ...CHROMIUM }, testMatch: MOBILE },
    { name: "iphone", use: { ...devices["iPhone 13"] }, testMatch: MOBILE },
  ],
});
