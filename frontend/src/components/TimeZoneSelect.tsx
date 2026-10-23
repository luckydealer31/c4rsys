import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { formatOffset, timeZoneOptions, zoneOffsetMinutes } from "../time";

/** Cities of a zone from the translations; a zone outside the offered list shows its name. */
export function useZoneCities(): (zone: string) => string {
  const { t } = useTranslation();
  return useCallback(
    (zone: string) =>
      t(`timezones.${zone}`, { defaultValue: zone.replaceAll("_", " ").replaceAll("/", " / ") }),
    [t],
  );
}

/** «UTC+05:00 — Yekaterinburg, Chelyabinsk, Perm». */
export function useZoneLabel(): (zone: string) => string {
  const cities = useZoneCities();
  return useCallback(
    (zone: string) => `${formatOffset(zoneOffsetMinutes(zone))} — ${cities(zone)}`,
    [cities],
  );
}

/** A time zone shown with its offset from UTC and its major cities, ordered by offset
 * (decision of 24.10.2026). */
export function TimeZoneSelect({
  value,
  onChange,
  disabled,
  emptyLabel,
}: {
  value: string;
  onChange: (zone: string) => void;
  disabled?: boolean;
  /** When given, an empty choice with this label is offered. */
  emptyLabel?: string;
}) {
  const cities = useZoneCities();
  const options = useMemo(() => timeZoneOptions(cities, value), [cities, value]);
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {emptyLabel !== undefined ? <option value="">{emptyLabel}</option> : null}
      {options.map((option) => (
        <option key={option.zone} value={option.zone}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
