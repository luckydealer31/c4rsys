import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatOffset, timeZoneOptions, zoneOffsetMinutes, zoneShort } from "../time";
import { IconButton } from "./ui";

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
 * (decision of 24.10.2026). Where the zone is already chosen it is written shortly — «UTC+7»,
 * the cities shown under the pointer (decision of 16.11.2026): a disabled field always; with
 * `compact`, until the pencil is pressed. */
export function TimeZoneSelect({
  value,
  onChange,
  disabled,
  emptyLabel,
  compact = false,
}: {
  value: string;
  onChange: (zone: string) => void;
  disabled?: boolean;
  /** When given, an empty choice with this label is offered. */
  emptyLabel?: string;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const cities = useZoneCities();
  const options = useMemo(() => timeZoneOptions(cities, value), [cities, value]);
  const [choosing, setChoosing] = useState(false);
  const full = options.find((option) => option.zone === value)?.label ?? value;
  if (disabled) return <input value={zoneShort(value)} title={full} disabled readOnly />;
  if (compact && value && !choosing) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong title={full}>{zoneShort(value)}</strong>
        <IconButton icon="edit" label={t("common.edit")} onClick={() => setChoosing(true)} />
      </span>
    );
  }
  return (
    <select
      value={value}
      onChange={(e) => {
        setChoosing(false);
        onChange(e.target.value);
      }}
    >
      {emptyLabel !== undefined ? <option value="">{emptyLabel}</option> : null}
      {options.map((option) => (
        <option key={option.zone} value={option.zone}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
