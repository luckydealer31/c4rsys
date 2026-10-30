import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { deviceTimeZone, formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";

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

/** The schedule of vehicles (decisions Б3, В-4): a row per vehicle with its bookings, rentals,
 * service and moves over a day, a week or a month; a click opens the record. */
export function SchedulePage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  const [span, setSpan] = useState<Span>("week");
  const [from, setFrom] = useState(() => dayStart(new Date()));
  const [location, setLocation] = useState("");
  const to = addDays(from, SPANS[span]);
  const query = `from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(to.toISOString())}${location ? `&location=${location}` : ""}`;
  const schedule = useApi<{ vehicles: { id: string; name: string }[]; items: Item[] }>(
    ["schedule", company.public_id, query],
    `/c/${company.public_id}/vehicles/schedule?${query}`,
  );
  const zone = deviceTimeZone();
  const when = (v: string) => formatMoment(v, zone, i18n.language);
  const total = to.getTime() - from.getTime();
  const ticks = span === "day" ? 24 : SPANS[span];
  const tickLabel = (index: number) => {
    const moment = span === "day" ? new Date(from.getTime() + (index * total) / ticks) : addDays(from, index);
    return span === "day"
      ? `${moment.getHours()}:00`
      : new Intl.DateTimeFormat(i18n.language, { day: "2-digit", month: "2-digit" }).format(moment);
  };
  const shift = (direction: number) => setFrom(addDays(from, direction * SPANS[span]));
  const now = Date.now();

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/vehicles")}>← {t("nav.garage")}</Link>
      </p>
      <h1>{t("schedule.title")}</h1>
      <Card>
        <div className="row">
          <Field label={t("schedule.span")}>
            <select value={span} onChange={(e) => setSpan(e.target.value as Span)}>
              {(Object.keys(SPANS) as Span[]).map((s) => (
                <option key={s} value={s}>
                  {t(`schedule.span_${s}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("schedule.location")}>
            <select value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">{t("schedule.all_locations")}</option>
              {(locations.data?.locations ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => shift(-1)}>
              ←
            </button>
            <button type="button" className="secondary" onClick={() => setFrom(dayStart(new Date()))}>
              {t("schedule.today")}
            </button>
            <button type="button" className="secondary" onClick={() => shift(1)}>
              →
            </button>
          </div>
        </div>
        <p className="muted small">
          {when(from.toISOString())} — {when(to.toISOString())}
        </p>
        <div className="small" style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
          {(Object.keys(COLORS) as Item["kind"][]).map((k) => (
            <span key={k}>
              <span style={{ display: "inline-block", width: 10, height: 10, background: COLORS[k], marginRight: 4 }} />
              {t(`schedule.kind_${k}`)}
            </span>
          ))}
        </div>
      </Card>
      {schedule.isLoading ? <Loading /> : null}
      <ErrorMessage error={schedule.error as never} />
      {schedule.data ? (
        <div className="card" style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 640 }}>
            <div style={{ display: "flex", marginLeft: 160 }}>
              {Array.from({ length: ticks }, (_, i) => (
                <div key={i} className="muted small" style={{ flex: 1, borderLeft: "1px solid var(--border, #ddd)", paddingLeft: 2 }}>
                  {span === "day" && i % 3 !== 0 ? "" : tickLabel(i)}
                </div>
              ))}
            </div>
            {schedule.data.vehicles.length === 0 ? <p className="muted">{t("schedule.no_vehicles")}</p> : null}
            {schedule.data.vehicles.map((v) => (
              <div key={v.id} style={{ display: "flex", alignItems: "center", borderTop: "1px solid var(--border, #ddd)", minHeight: 34 }}>
                <div style={{ width: 160, flexShrink: 0, paddingRight: 8 }} className="small">
                  <Link to={companyPath(company, `/vehicles/${v.id}`)}>{v.name}</Link>
                </div>
                <div style={{ position: "relative", flex: 1, height: 28 }}>
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
                            top: 3,
                            height: 22,
                            left: `${left}%`,
                            width: `${width}%`,
                            background: COLORS[item.kind],
                            opacity: item.planned ? 0.55 : 1,
                            border: item.planned ? "1px dashed #fff" : "none",
                            borderRadius: 4,
                            color: "#fff",
                            fontSize: 11,
                            overflow: "hidden",
                            whiteSpace: "nowrap",
                            padding: "3px 4px",
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
        </div>
      ) : null}
      <p className="muted small">{t("schedule.hint")}</p>
    </div>
  );
}
