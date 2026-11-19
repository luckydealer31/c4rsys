import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "./icons";
import { MomentField } from "./MomentField";
import { Info } from "./ui";
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
 * calendar — a small window with the ready periods and «from» and «to» for an own one. `from`
 * and `to` are values of datetime-local fields — of date fields with `days` — empty: no period.
 * What each ready period means is decided by the page: `onPick` sets its dates. */
export function PeriodControl({
  modes,
  mode,
  onPick,
  from,
  to,
  onFrom,
  onTo,
  opening,
  hint,
  days = false,
}: {
  modes: readonly string[];
  /** One of `modes`, or «own» for a period typed by hand. */
  mode: string;
  onPick: (mode: string) => void;
  from: string;
  to: string;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
  opening?: (date: string) => string;
  hint?: string;
  /** The period is of whole days: no time is asked or shown. */
  days?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, root);
  const name = (key: string) => t(`period.mode_${key}`);
  const start = opening ?? (() => "00:00");
  const next = modes[(modes.indexOf(mode) + 1) % modes.length] ?? modes[0] ?? "";
  // «20.11 09:00» of a local value, in the manner of the language.
  const short = (value: string) => {
    const [date = "", time = "00:00"] = value.split("T");
    const [y = 0, m = 1, d = 1] = date.split("-").map(Number);
    const [hh = 0, mm = 0] = time.split(":").map(Number);
    return new Intl.DateTimeFormat(i18n.language, {
      day: "2-digit",
      month: "2-digit",
      ...(days ? {} : { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
      timeZone: "UTC",
    }).format(new Date(Date.UTC(y, m - 1, d, hh, mm)));
  };
  const dates = [from && short(from), to && short(to)].filter(Boolean).join(" – ");
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
            {hint ? <Info text={hint} /> : null}
          </div>
          <span className="period-label">{t("period.from")}</span>
          {days ? (
            <input type="date" value={from} max={to || undefined} aria-label={t("period.from_label")} onChange={(e) => onFrom(e.target.value)} />
          ) : (
            <MomentField value={from} onChange={onFrom} opening={start} label={t("period.from_label")} />
          )}
          <span className="period-label">{t("period.to")}</span>
          {days ? (
            <input type="date" value={to} min={from || undefined} aria-label={t("period.to_label")} onChange={(e) => onTo(e.target.value)} />
          ) : (
            <MomentField value={to} onChange={onTo} opening={start} label={t("period.to_label")} />
          )}
        </div>
      ) : null}
    </div>
  );
}
