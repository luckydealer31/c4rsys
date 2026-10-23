import { useMemo } from "react";

import { timeZoneOptions } from "../time";

/** A time zone shown with its offset from UTC, ordered by offset (decision of 24.10.2026). */
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
  const options = useMemo(() => timeZoneOptions(), []);
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
