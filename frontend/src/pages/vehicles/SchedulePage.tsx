import { useCallback, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate } from "react-router";

import { useApi } from "../../api/queries";
import { Calendar } from "../../components/Calendar";
import { Icon } from "../../components/icons";
import { Card, ErrorMessage, Loading } from "../../components/ui";
import { useDismiss } from "../../components/useDismiss";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { workHome } from "../../layout/CompanyTabs";
import { deviceTimeZone, formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "./VehiclesPage";

type Item = {
  card_id: string;
  kind: "booking" | "rental" | "service" | "delivery";
  id: string;
  start: string;
  end: string;
  state: string;
  label: string;
  planned: boolean;
};

const SPANS = { day: 1, week: 7, month: 30 } as const;
type Span = keyof typeof SPANS;
const COLORS: Record<Item["kind"], string> = {
  booking: "#6c8ef5",
  rental: "#2f6fed",
  service: "#e39b2d",
  delivery: "#3aa676",
};
const PATHS: Record<Item["kind"], string> = {
  booking: "/rentals/",
  rental: "/rentals/",
  service: "/service/operations/",
  delivery: "/trips/",
};

/** Start of the day of a moment in the browser's time zone. */
function dayStart(value: Date): Date {
  const result = new Date(value);
  result.setHours(0, 0, 0, 0);
  return result;
}

/** The same time of day `days` calendar days later: a day is not always 24 hours. */
function addDays(value: Date, days: number): Date {
  const result = new Date(value);
  result.setDate(result.getDate() + days);
  return result;
}

/** The schedule is a tab of «Rental» — of «Service» without rental (decision of 14.11.2026). */
export function SchedulePage() {
  const company = useCompany();
  return <Navigate to={companyPath(company, `${workHome(company)}?tab=schedule`)} replace />;
}

/** The schedule as a tab of «Rental» or «Service»: own and managed vehicles, each switched on
 * and off as in the garage; the last one that is on stays on. */
/** What the colours of the strip mean. */
export function ScheduleLegend() {
  const { t } = useTranslation();
  return (
    <div className="schedule-legend small" role="note" aria-label={t("schedule.legend")}>
      {(Object.keys(COLORS) as Item["kind"][]).map((k) => (
        <span key={k}>
          <span className="schedule-mark" style={{ background: COLORS[k] }} />
          {t(`schedule.kind_${k}`)}
        </span>
      ))}
    </div>
  );
}

export function ScheduleTab({ legend = true }: { legend?: boolean }) {
  const { t } = useTranslation();
  const [shown, setShown] = useState<Set<string>>(new Set(["own", "managed"]));
  const toggle = (key: string) => {
    if (shown.has(key) && shown.size === 1) return;
    const next = new Set(shown);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setShown(next);
  };
  // The switches stand in the bar of the schedule, with its other controls (customer's remark
  // of 20.11.2026).
  return (
    <ScheduleView
      relations={shown}
      legend={legend}
      filters={
        // One joined pair without marks: what is outlined is shown — both, in one outline.
        <div className="segmented outlined" role="group" aria-label={t("schedule.kinds")}>
          {["own", "managed"].map((key) => (
            <button key={key} type="button" aria-pressed={shown.has(key)} onClick={() => toggle(key)}>
              {t(`vehicles.tab_${key}`)}
            </button>
          ))}
        </div>
      }
    />
  );
}

/** The schedule of vehicles (decisions Б3, В-4): a row per vehicle with its bookings, rentals,
 * service and moves over a day, a week or a month; a click opens the record. Only the vehicles
 * of the chosen relations are shown, grouped by location as in the list of the garage. */
export function ScheduleView({
  relations,
  filters,
  legend = true,
}: {
  relations: Set<string>;
  filters?: ReactNode;
  /** Whether the colours are explained under the bar; a page may show them elsewhere. */
  legend?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const [span, setSpan] = useState<Span>("week");
  // The chosen day (customer's decisions of 20.11.2026): a day shows it whole; a week has it in
  // the middle — three days before and three after; a month is its calendar month. It is today
  // at first, and pressing the chosen span again brings today back.
  const [day, setDay] = useState(() => dayStart(new Date()));
  // A period of its own, chosen in the calendar: from its first day to the day after its last
  // one. It may be of any length — the strip shows a month at most and the rest is scrolled.
  const [own, setOwn] = useState<{ from: Date; to: Date } | null>(null);
  const [picking, setPicking] = useState(false);
  const picker = useRef<HTMLDivElement>(null);
  const closePicker = useCallback(() => setPicking(false), []);
  useDismiss(picking, closePicker, picker);
  // The locations switched off; none at first.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [choosing, setChoosing] = useState(false);
  const places = useRef<HTMLDivElement>(null);
  const closePlaces = useCallback(() => setChoosing(false), []);
  useDismiss(choosing, closePlaces, places);
  const from = own ? own.from : span === "day" ? day : span === "week" ? addDays(day, -3) : new Date(day.getFullYear(), day.getMonth(), 1);
  const to = own
    ? own.to
    : span === "day"
      ? addDays(day, 1)
      : span === "week"
        ? addDays(day, 4)
        : new Date(day.getFullYear(), day.getMonth() + 1, 1);
  const days = Math.round((to.getTime() - from.getTime()) / 86_400_000);
  // A single day is shown by the hours.
  const hours = days === 1;
  const local = (value: Date) =>
    `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
  const dayText = (value: Date) =>
    new Intl.DateTimeFormat(i18n.language, { day: "2-digit", month: "2-digit", year: "numeric" }).format(value);
  const query = `from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}`;
  const schedule = useApi<{ vehicles: { id: string; name: string }[]; items: Item[] }>(
    ["schedule", company.public_id, query],
    `/c/${company.public_id}/vehicles/schedule?${query}`,
  );
  const zone = deviceTimeZone();
  const when = (v: string) => formatMoment(v, zone, i18n.language);
  const total = to.getTime() - from.getTime();
  const ticks = hours ? 24 : days;
  const tickLabel = (index: number) => {
    const moment = hours ? new Date(from.getTime() + (index * total) / ticks) : addDays(from, index);
    if (hours) return `${moment.getHours()}:00`;
    if (days <= 7) return new Intl.DateTimeFormat(i18n.language, { weekday: "short", day: "2-digit", month: "2-digit" }).format(moment);
    // Many columns leave room for the day of the month only; a new month is named on its first day.
    return moment.getDate() === 1 && own
      ? new Intl.DateTimeFormat(i18n.language, { day: "numeric", month: "2-digit" }).format(moment)
      : String(moment.getDate());
  };
  // The arrows turn a day, a week of seven days or a calendar month.
  const shift = (direction: number) => {
    // A period of its own is turned by its own length.
    if (own) setOwn({ from: addDays(own.from, direction * days), to: addDays(own.to, direction * days) });
    else setDay(span === "month" ? new Date(day.getFullYear(), day.getMonth() + direction, 1) : addDays(day, direction * SPANS[span]));
  };
  const now = Date.now();
  // The relation and the location of a row come from the list of the garage.
  const cards = useVehicles();
  const info = new Map((cards.data?.vehicles ?? []).map((v) => [v.id, v]));
  const rows = (schedule.data?.vehicles ?? []).filter((v) => {
    const card = info.get(v.id);
    return card ? relations.has(card.relation) : true;
  });
  const known = locations.data?.locations ?? [];
  const groups = [
    ...known
      .filter((l) => !hidden.has(l.id))
      .map((l) => ({ id: l.id, name: l.name, rows: rows.filter((v) => info.get(v.id)?.location_id === l.id) })),
    {
      id: "none",
      name: t("vehicles.no_location"),
      // The vehicles of no location are shown while every location is.
      rows: hidden.size ? [] : rows.filter((v) => !known.some((l) => l.id === info.get(v.id)?.location_id)),
    },
  ].filter((group) => group.rows.length > 0);
  const togglePlace = (id: string) => {
    const next = new Set(hidden);
    if (next.has(id)) next.delete(id);
    else if (next.size < known.length - 1) next.add(id);
    setHidden(next);
  };

  return (
    <>
      <Card>
        {/* One line (customer's remarks of 20.11.2026): the locations in a list at the left —
            all, one or several, there may be dozens of them; the span and the date with its
            arrows at the right. */}
        <div className="schedule-bar">
          {known.length > 1 ? (
            <div className="schedule-places" ref={places}>
              <button type="button" className="secondary places-button" aria-expanded={choosing} aria-label={t("schedule.location")} onClick={() => setChoosing(!choosing)}>
                {hidden.size === 0
                  ? t("schedule.all_locations")
                  : known.length - hidden.size === 1
                    ? (known.find((l) => !hidden.has(l.id))?.name ?? "")
                    : t("schedule.locations_count", { count: known.length - hidden.size })}
                <span aria-hidden="true">▾</span>
              </button>
              {choosing ? (
                <div className="popup places-pop" role="dialog" aria-label={t("schedule.location")}>
                  <label className="check">
                    <input type="checkbox" checked={hidden.size === 0} onChange={() => setHidden(new Set())} /> {t("schedule.all_locations")}
                  </label>
                  {known.map((l) => (
                    <label key={l.id} className="check">
                      <input type="checkbox" checked={!hidden.has(l.id)} onChange={() => togglePlace(l.id)} /> {l.name}
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {filters ? <div className="schedule-filters">{filters}</div> : null}
          <div className="schedule-right">
            <div className="segmented" role="group" aria-label={t("schedule.span")}>
              {(Object.keys(SPANS) as Span[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={!own && span === s}
                  onClick={() => {
                    // The chosen span pressed again: back to today.
                    if (!own && span === s) setDay(dayStart(new Date()));
                    setOwn(null);
                    setSpan(s);
                  }}
                >
                  {t(`schedule.span_${s}`)}
                </button>
              ))}
            </div>
          <div className="actions schedule-nav">
            <button type="button" className="secondary" aria-label={t("schedule.back")} onClick={() => shift(-1)}>
              ←
            </button>
            {/* One calendar for every span: a date is the day itself, the middle of the week or
                its month; two dates are a period of its own. */}
            <div className="schedule-picker" ref={picker}>
              <button
                type="button"
                className="secondary schedule-date"
                aria-label={t("schedule.go_to")}
                aria-expanded={picking}
                data-from={local(own ? own.from : day)}
                data-to={local(own ? addDays(own.to, -1) : day)}
                onClick={() => setPicking(!picking)}
              >
                <Icon name="calendar" size={18} /> {own ? `${dayText(own.from)} – ${dayText(addDays(own.to, -1))}` : dayText(day)}
              </button>
              {picking ? (
                <div className="popup schedule-pop" role="dialog" aria-label={t("schedule.go_to")}>
                  <Calendar
                    from={local(own ? own.from : day)}
                    to={own ? local(addDays(own.to, -1)) : ""}
                    today={local(new Date())}
                    onCancel={closePicker}
                    onOk={(a, b) => {
                      const first = dayStart(new Date(`${a}T00:00`));
                      if (a === b) {
                        setOwn(null);
                        setDay(first);
                      } else {
                        setOwn({ from: first, to: addDays(dayStart(new Date(`${b}T00:00`)), 1) });
                      }
                      closePicker();
                    }}
                  />
                </div>
              ) : null}
            </div>
            <button type="button" className="secondary" aria-label={t("schedule.forward")} onClick={() => shift(1)}>
              →
            </button>
          </div>
          </div>
        </div>
        {/* The dates are read from the strip itself; the colours are explained beside the
            buttons of the section when the page has them (customer's remarks of 20.11.2026). */}
        {legend ? <ScheduleLegend /> : null}
      </Card>
      {schedule.isLoading ? <Loading /> : null}
      <ErrorMessage error={schedule.error as never} />
      {schedule.data ? (
        <div className="card schedule-strip" style={{ overflowX: "auto" }}>
          {/* A month fills the width; a longer period is as much wider and is scrolled. */}
          <div style={{ minWidth: 760, paddingRight: 24, width: days > 31 ? `calc(200px + (100% - 224px) * ${days / 31})` : undefined }}>
            <div style={{ display: "flex", marginLeft: 200, position: "relative" }}>
              {Array.from({ length: ticks }, (_, i) => (
                <div key={i} className="muted small" style={{ flex: 1, borderLeft: "1px solid var(--border, #ddd)", paddingLeft: 2 }}>
                  {hours && i % 2 !== 0 ? "" : tickLabel(i)}
                </div>
              ))}
              {/* The end of the day is marked too. */}
              {hours ? (
                <div className="muted small" style={{ position: "absolute", right: 0, top: 0, transform: "translateX(50%)" }}>
                  24:00
                </div>
              ) : null}
            </div>
            {rows.length === 0 ? <p className="muted">{t("schedule.no_vehicles")}</p> : null}
            {groups.map((group) => (
              <div key={group.id}>
                <div className="small" style={{ fontWeight: 600, padding: "8px 0 4px", borderTop: "1px solid var(--border, #ddd)" }}>
                  {group.name}
                </div>
                {group.rows.map((v) => (
              <div key={v.id} style={{ display: "flex", alignItems: "center", borderTop: "1px solid var(--border, #ddd)", minHeight: 44 }}>
                <div className="schedule-name" style={{ width: 200, flexShrink: 0, paddingRight: 8 }}>
                  <Link to={companyPath(company, `/vehicles/${v.id}`)}>{v.name}</Link>
                </div>
                <div style={{ position: "relative", flex: 1, height: 36 }}>
                  {now >= from.getTime() && now < to.getTime() ? (
                    <div
                      style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: "#d33", left: `${((now - from.getTime()) / total) * 100}%` }}
                    />
                  ) : null}
                  {schedule.data?.items
                    .filter((item) => item.card_id === v.id)
                    .map((item) => {
                      const begin = Math.max(new Date(item.start).getTime(), from.getTime());
                      const finish = Math.min(new Date(item.end).getTime(), to.getTime());
                      const left = ((begin - from.getTime()) / total) * 100;
                      const width = Math.max(((finish - begin) / total) * 100, 0.8);
                      return (
                        <Link
                          key={`${item.kind}-${item.id}`}
                          to={companyPath(company, `${PATHS[item.kind]}${item.id}`)}
                          title={`${t(`schedule.kind_${item.kind}`)} ${item.label} · ${when(item.start)} — ${when(item.end)}`}
                          style={{
                            position: "absolute",
                            top: 4,
                            height: 28,
                            left: `${left}%`,
                            width: `${width}%`,
                            background: COLORS[item.kind],
                            opacity: item.planned ? 0.55 : 1,
                            border: item.planned ? "1px dashed #fff" : "none",
                            borderRadius: 4,
                            color: "#fff",
                            fontSize: 12,
                            overflow: "hidden",
                            whiteSpace: "nowrap",
                            padding: "5px 6px",
                            textDecoration: "none",
                          }}
                        >
                          {item.label || t(`schedule.kind_${item.kind}`)}
                        </Link>
                      );
                    })}
                </div>
              </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <p className="muted small">{t("schedule.hint")}</p>
    </>
  );
}
