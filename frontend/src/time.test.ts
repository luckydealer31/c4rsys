import { describe, expect, it } from "vitest";

import { formatOffset, timeZoneOptions, zoneOffsetMinutes } from "./time";

describe("time zones with offsets", () => {
  it("computes the current offset, summer time included", () => {
    const winter = new Date("2026-01-15T12:00:00Z");
    const summer = new Date("2026-07-15T12:00:00Z");
    expect(zoneOffsetMinutes("Asia/Yekaterinburg", winter)).toBe(300);
    expect(zoneOffsetMinutes("Europe/Berlin", winter)).toBe(60);
    expect(zoneOffsetMinutes("Europe/Berlin", summer)).toBe(120);
    expect(zoneOffsetMinutes("America/St_Johns", winter)).toBe(-210);
  });

  it("formats an offset like UTC+05:00", () => {
    expect(formatOffset(300)).toBe("UTC+05:00");
    expect(formatOffset(-210)).toBe("UTC−03:30");
    expect(formatOffset(0)).toBe("UTC+00:00");
  });

  it("offers a short list ordered by offset, keeping a current value outside it", () => {
    const winter = new Date("2026-01-15T12:00:00Z");
    const options = timeZoneOptions((zone) => `cities of ${zone}`, "Asia/Barnaul", winter);
    const offsets = options.map((o) => o.offset);
    expect(offsets).toEqual([...offsets].sort((a, b) => a - b));
    expect(options.length).toBeLessThan(80);
    expect(options.find((o) => o.zone === "Asia/Yekaterinburg")?.label).toBe(
      "UTC+05:00 — cities of Asia/Yekaterinburg",
    );
    expect(options.find((o) => o.zone === "Asia/Kolkata")?.offset).toBe(330);
    expect(options.some((o) => o.zone === "Asia/Barnaul")).toBe(true);
  });
});
