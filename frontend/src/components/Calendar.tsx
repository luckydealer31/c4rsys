import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const DAY = 86_400_000;
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const utc = (date: string) => new Date(`${date}T00:00:00Z`).getTime();

/** Our own calendar (customer's decisions of 20.11.2026): a date pressed and «OK» — one date; a
 * date, then another one — after turning a month or a year, too — and «OK» — a period; while the
 * second date is being looked for, the days up to the pointer are marked. The month and the year
 * stand on top: single arrows turn a month, double arrows a year; a swipe turns a month. Dates
 * are «2026-11-20»; `onOk` gets the first and the last day, equal for one date. Closing without
 * «OK» changes nothing. */
export function Calendar({
  from,
  to,
  today,
  onOk,
  onCancel,
  onReset,
  single = false,
}: {
  from: string;
  to: string;
  /** The day that is today for the reader, marked in the grid. */
  today: string;
  onOk: (from: string, to: string) => void;
  onCancel: () => void;
  /** «Reset» in small letters: no dates at all. Not offered without it. */
  onReset?: () => void;
  /** Only one date may be chosen. */
  single?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [first, setFirst] = useState(from);
  const [last, setLast] = useState(to && to !== from ? to : "");
  const [over, setOver] = useState("");
  const [shown, setShown] = useState(() => (from || today).slice(0, 7));
  const swipe = useRef<number | null>(null);
  const [year = 2000, month = 1] = shown.split("-").map(Number);
  const turn = (months: number) => setShown(new Date(Date.UTC(year, month - 1 + months, 1)).toISOString().slice(0, 7));

  // The week begins as the language has it: Monday, or Sunday.
  const locale = new Intl.Locale(i18n.language) as Intl.Locale & { weekInfo?: { firstDay: number }; getWeekInfo?: () => { firstDay: number } };
  const firstDay = (locale.getWeekInfo?.() ?? locale.weekInfo)?.firstDay ?? 1;
  const start = Date.UTC(year, month - 1, 1);
  const lead = (new Date(start).getUTCDay() - (firstDay % 7) + 7) % 7;
  const count = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const cells = Array.from({ length: Math.ceil((lead + count) / 7) * 7 }, (_, i) => iso(start + (i - lead) * DAY));
  const weekday = new Intl.DateTimeFormat(i18n.language, { weekday: "short", timeZone: "UTC" });
  // «Ноябрь 2026»: the month named by itself, with a capital letter, and the year.
  const named = new Intl.DateTimeFormat(i18n.language, { month: "long", timeZone: "UTC" }).format(start);
  const title = `${named.charAt(0).toUpperCase()}${named.slice(1)} ${year}`;
  const long = new Intl.DateTimeFormat(i18n.language, { dateStyle: "long", timeZone: "UTC" });
  const short = new Intl.DateTimeFormat(i18n.language, { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });

  // Whether the first date of a new choice is pressed and the second one may follow. A date
  // the calendar opened with is not one: the first press begins the choice anew.
  const [begun, setBegun] = useState(false);
  function press(date: string) {
    if (single || !begun) {
      setFirst(date);
      setLast("");
      setBegun(!single);
      return;
    }
    if (date < first) {
      setLast(first);
      setFirst(date);
    } else if (date > first) {
      setLast(date);
    }
    setBegun(false);
  }
  // What is marked: the chosen period, or the days up to the pointer while the end is looked for.
  const edge = last || (begun && over ? over : "");
  const [low, high] = edge && edge < first ? [edge, first] : [first, edge || first];
  const chosen = first ? (last ? `${short.format(utc(first))} – ${short.format(utc(last))}` : short.format(utc(first))) : "";

  return (
    <div
      className="calendar"
      onTouchStart={(e) => (swipe.current = e.touches[0]?.clientX ?? null)}
      onTouchEnd={(e) => {
        const moved = (e.changedTouches[0]?.clientX ?? 0) - (swipe.current ?? 0);
        if (swipe.current !== null && Math.abs(moved) > 60) turn(moved < 0 ? 1 : -1);
        swipe.current = null;
      }}
    >
      <div className="calendar-head">
        <button type="button" className="icon-button" aria-label={t("calendar.prev_year")} onClick={() => turn(-12)}>
          «
        </button>
        <button type="button" className="icon-button" aria-label={t("calendar.prev_month")} onClick={() => turn(-1)}>
          ‹
        </button>
        <strong aria-live="polite">{title}</strong>
        <button type="button" className="icon-button" aria-label={t("calendar.next_month")} onClick={() => turn(1)}>
          ›
        </button>
        <button type="button" className="icon-button" aria-label={t("calendar.next_year")} onClick={() => turn(12)}>
          »
        </button>
      </div>
      <div className="calendar-grid" onMouseLeave={() => setOver("")}>
        {cells.slice(0, 7).map((date) => (
          <span key={date} className="calendar-weekday" aria-hidden="true">
            {weekday.format(utc(date))}
          </span>
        ))}
        {cells.map((date) => {
          const inside = date.slice(0, 7) === shown;
          const marked = Boolean(first) && date >= low && date <= high;
          return (
            <button
              key={date}
              type="button"
              data-date={date}
              className={[
                "calendar-day",
                inside ? "" : "outside",
                marked ? "marked" : "",
                date === low || date === high ? "edge" : "",
                date === today ? "today" : "",
              ].join(" ")}
              aria-label={long.format(utc(date))}
              aria-pressed={date === first || date === last}
              onMouseEnter={() => setOver(date)}
              onClick={() => press(date)}
            >
              {Number(date.slice(8))}
            </button>
          );
        })}
      </div>
      <div className="muted small calendar-chosen">{chosen || t("calendar.pick")}</div>
      <div className="calendar-foot">
        {onReset ? (
          <button type="button" className="link small" onClick={onReset}>
            {t("calendar.reset")}
          </button>
        ) : (
          <span />
        )}
        <span className="calendar-buttons">
          <button type="button" className="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button type="button" disabled={!first} onClick={() => onOk(first, last || first)}>
            {t("calendar.ok")}
          </button>
        </span>
      </div>
    </div>
  );
}
