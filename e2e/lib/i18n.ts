import { readFileSync } from "node:fs";

// The interface texts are taken from the generated Russian locale, so the test follows the
// texts of the product instead of copying them.
const ru = JSON.parse(readFileSync("/locales/ru.json", "utf8")) as Record<string, unknown>;

export function t(key: string, params: Record<string, string | number> = {}): string {
  const value = key.split(".").reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], ru);
  if (typeof value !== "string") throw new Error(`No text for ${key}`);
  return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(params[name] ?? ""));
}
