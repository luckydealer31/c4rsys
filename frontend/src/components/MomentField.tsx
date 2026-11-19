import { useTranslation } from "react-i18next";

import type { ContextType, Location, Schedule } from "../api/types";

const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

/** When the schedule opens on a date («2026-11-25»): the exception of the date or the day of the
 * week (§ 4.2.3); null — closed that day. */
export function openingOn(schedule: Schedule, date: string): string | null {
  const exception = schedule.exceptions.find((item) => item.date === date);
  const day = DAYS[new Date(`${date}T00:00:00Z`).getUTCDay()] ?? "mon";
  const intervals = exception ? exception.intervals : (schedule.weekly[day] ?? []);
  return intervals.length ? (intervals.map((item) => item.from).sort()[0] ?? null) : null;
}

/** The earliest opening of a direction among the locations on a date; 00:00 when there is none
 * (customer's decision of 20.11.2026: a calendar begins a day with the working time). */
export function openingTime(locations: Location[], type: ContextType, date: string): string {
  const times = locations
    .map((location) => {
      const context = location.contexts.find((item) => item.type === type);
      return context ? openingOn(context.schedule.own ?? location.schedule, date) : null;
    })
    .filter((time): time is string => Boolean(time))
    .sort();
  return (times[0] ?? "00:00").slice(0, 5);
}

/** A moment chosen as a date and a time: the value is that of a datetime-local field
 * («2026-11-25T09:00») or empty. A date chosen while there is no time takes the time `opening`
 * gives for it — the beginning of the working day. */
export function MomentField({
  value,
  onChange,
  opening,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  opening: (date: string) => string;
  label: string;
}) {
  const { t } = useTranslation();
  const [date = "", time = ""] = value.split("T");
  return (
    <span className="moment-field">
      <input
        type="date"
        value={date}
        aria-label={`${label}: ${t("moment.date")}`}
        onChange={(e) => onChange(e.target.value ? `${e.target.value}T${time || opening(e.target.value)}` : "")}
      />
      <input
        type="time"
        value={time}
        disabled={!date}
        aria-label={`${label}: ${t("moment.time")}`}
        onChange={(e) => onChange(`${date}T${e.target.value || opening(date)}`)}
      />
    </span>
  );
}
