import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Calendar } from "./Calendar";
import { Icon } from "./icons";
import { useDismiss } from "./useDismiss";

const DAY = 86_400_000;
const utc = (date: string) => new Date(`${date}T00:00:00Z`).getTime();
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

/** The days chosen for a list, remembered in the browser under `key` (customer's decision of
 * 20.11.2026: the next visit begins with the last choice). */
export function useStoredDates(key: string): [string, string, (from: string, to: string) => void] {
  const read = (): [string, string] => {
    try {
      const [from = "", to = ""] = (window.localStorage.getItem(key) ?? "").split("|");
      return /^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to || from) ? [from, to || from] : ["", ""];
    } catch {
      return ["", ""];
    }
  };
  const [dates, setDates] = useState<[string, string]>(read);
  const set = (from: string, to: string) => {
    setDates([from, to]);
    try {
      if (from) window.localStorage.setItem(key, `${from}|${to}`);
      else window.localStorage.removeItem(key);
    } catch {
      // Without the storage the choice lives till the page is left.
    }
  };
  return [dates[0], dates[1], set];
}

/** The days of a list in one compact control of the filter line (customer's decisions of
 * 20.11.2026). With no date it is the calendar alone and the list shows everything. The calendar
 * opens our calendar with today chosen: «OK» is today, another date or a period of any length
 * may be pressed, «Cancel» leaves things as they are and the small «Reset» takes the dates away.
 * A chosen date or period is shown beside the calendar, with arrows that turn it back and
 * forward by its own length. `from` and `to` are days («2026-11-20»); empty — all. */
export function PeriodControl({
  from,
  to,
  onDates,
  today,
}: {
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
  const last = to || from;
  const short = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }).format(utc(value));
  const dates = from ? (last !== from ? `${short(from)} – ${short(last)}` : short(from)) : "";
  const turn = (direction: number) => {
    const length = utc(last) - utc(from) + DAY;
    onDates(iso(utc(from) + direction * length), iso(utc(last) + direction * length));
  };
  const pick = (a: string, b: string) => {
    onDates(a, b);
    close();
  };
  return (
    <div className={`period-control${from ? "" : " empty"}`} ref={root} role="group" aria-label={t("period.title")}>
      {from ? (
        <button type="button" aria-label={t("period.back")} onClick={() => turn(-1)}>
          ←
        </button>
      ) : null}
      <button
        type="button"
        className="period-calendar"
        aria-label={t("period.choose")}
        aria-expanded={open}
        data-from={from}
        data-to={from ? last : ""}
        data-tip={open || from ? undefined : t("period.choose")}
        onClick={() => setOpen(!open)}
      >
        <Icon name="calendar" size={18} />
        {dates ? <span>{dates}</span> : null}
      </button>
      {from ? (
        <button type="button" aria-label={t("period.forward")} onClick={() => turn(1)}>
          →
        </button>
      ) : null}
      {open ? (
        <div className="popup period-pop" role="dialog" aria-label={t("period.choose")}>
          {/* The calendar offers today whatever is chosen now: «OK» is today at once. */}
          <Calendar from={today} to={today} today={today} onOk={pick} onCancel={close} onReset={() => pick("", "")} />
        </div>
      ) : null}
    </div>
  );
}
