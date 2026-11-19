import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "./icons";
import { Calendar } from "./Calendar";
import { useDismiss } from "./useDismiss";

const DAY = 86_400_000;
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

/** The days of a ready period ahead, as date fields give them (customer's decisions of
 * 20.11.2026): today, tomorrow; 7 days — today and the six days after it; a month — from today
 * to the day before the same date of the next month. `today` is «2026-11-20». */
export function daysAhead(mode: string, today: string): [string, string] {
  const start = new Date(`${today}T00:00:00Z`).getTime();
  if (mode === "today") return [today, today];
  if (mode === "tomorrow") return [iso(start + DAY), iso(start + DAY)];
  if (mode === "week") return [today, iso(start + 6 * DAY)];
  if (mode === "month") {
    const [y = 0, m = 1, d = 1] = today.split("-").map(Number);
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return [today, iso(Date.UTC(y, m, Math.min(d, last)) - DAY)];
  }
  return ["", ""];
}

/** The period of a list in one compact control of the filter line (customer's decisions of
 * 20.11.2026): the name of the period — a press goes round the ready periods; its dates; the
 * calendar — a small window with the ready periods and our calendar, where one date or a period
 * of any length is chosen. `from` and `to` are days («2026-11-20»), empty — no period. What each
 * ready period means is decided by the page: `onPick` sets its dates. */
export function PeriodControl({
  modes,
  mode,
  onPick,
  from,
  to,
  onDates,
  today,
}: {
  modes: readonly string[];
  /** One of `modes`, or «own» for dates chosen in the calendar. */
  mode: string;
  onPick: (mode: string) => void;
  from: string;
  to: string;
  onDates: (from: string, to: string) => void;
  today: string;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, root);
  const name = (key: string) => t(`period.mode_${key}`);
  const next = modes[(modes.indexOf(mode) + 1) % modes.length] ?? modes[0] ?? "";
  const short = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(
      new Date(`${value}T00:00:00Z`),
    );
  const dates = from && to && from !== to ? `${short(from)} – ${short(to)}` : from || to ? short(from || to) : "";
  return (
    <div className="period-control" ref={root} role="group" aria-label={t("period.title")}>
      <button type="button" className="period-name" aria-label={`${t("period.title")}: ${name(mode)}`} onClick={() => onPick(next)}>
        {name(mode)}
      </button>
      {dates ? <span className="period-dates">{dates}</span> : null}
      <button
        type="button"
        className="period-calendar"
        aria-label={t("period.choose")}
        aria-expanded={open}
        data-tip={open ? undefined : t("period.choose")}
        onClick={() => setOpen(!open)}
      >
        <Icon name="calendar" size={18} />
      </button>
      {open ? (
        <div className="popup period-pop" role="dialog" aria-label={t("period.choose")}>
          <div className="period-ready">
            {modes.map((key) => (
              <button key={key} type="button" className="toggle-chip plain" aria-pressed={key === mode} onClick={() => onPick(key)}>
                {name(key)}
              </button>
            ))}
          </div>
          <Calendar
            key={`${from}-${to}`}
            from={from}
            to={to}
            today={today}
            onOk={(a, b) => {
              onDates(a, b);
              close();
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
