/** Time helpers: moments come from the server in UTC and are shown in the time zone of the
 * location (§ 4.2.2); in the history with the year (§ 2.7.6). */

export function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function timeZones(): string[] {
  const zones = Intl.supportedValuesOf?.("timeZone") ?? [];
  return zones.includes("UTC") ? zones : ["UTC", ...zones];
}

export function formatMoment(value: string | null | undefined, timeZone: string, locale: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** Value for <input type="datetime-local"> of a moment in a time zone. */
export function toLocalInput(moment: Date, timeZone: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(moment)
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

/** An ISO moment with its offset for a local date and time entered in a time zone. */
export function fromLocalInput(value: string, timeZone: string): string {
  const [date, time] = value.split("T");
  const [y, m, d] = (date ?? "").split("-").map(Number);
  const [hh, mm] = (time ?? "").split(":").map(Number);
  const asUtc = Date.UTC(y ?? 0, (m ?? 1) - 1, d ?? 1, hh ?? 0, mm ?? 0);
  // Offset of the zone at that moment; one correction step covers DST changes.
  const offset = (at: number) => {
    const shown = toLocalInput(new Date(at), timeZone);
    const [sd, st] = shown.split("T");
    const [sy, sm, sdd] = (sd ?? "").split("-").map(Number);
    const [sh, smin] = (st ?? "").split(":").map(Number);
    return Date.UTC(sy ?? 0, (sm ?? 1) - 1, sdd ?? 1, sh ?? 0, smin ?? 0) - at;
  };
  let instant = asUtc - offset(asUtc);
  instant = asUtc - offset(instant);
  return new Date(instant).toISOString();
}

/** ISO 4217 codes known to the browser; the server checks them against its reference. */
export function currencies(): string[] {
  return Intl.supportedValuesOf?.("currency") ?? ["THB", "USD", "EUR", "RUB"];
}
