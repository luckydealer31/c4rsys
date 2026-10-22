import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import ru from "./locales/ru.json";
import th from "./locales/th.json";

function keys(obj: object, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([key, value]) =>
    typeof value === "object" && value !== null
      ? keys(value as object, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

describe("translations", () => {
  it("every language has the same keys as English", () => {
    const reference = keys(en).sort();
    expect(keys(ru).sort()).toEqual(reference);
    expect(keys(th).sort()).toEqual(reference);
  });
});
