import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import type { Booking, Client, ExtraService, ServiceLine } from "../../api/types";
import { useApi } from "../../api/queries";
import { Icon } from "../../components/icons";
import { Card, Loading, Switch } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { SectionTabs } from "../../layout/SectionTabs";
import { useFirstTab } from "../../layout/tabOrder";
import { formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useOrders } from "../orders/OrdersPage";
import { SectionSearch } from "../search/SearchPage";
import { useLocations } from "../settings/LocationsPage";
import { useTagMatch } from "../tags/Tags";
import { rangesText, unitText, useTariffs } from "../tariffs/TariffsPage";
import { Park } from "./Park";
import { serviceTerms } from "./Services";
import { mayTrips, TripsView } from "../trips/TripsPage";
import { ScheduleTab } from "../vehicles/SchedulePage";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";

// The lists of the section — rentals with bookings, and the ordered services — are filtered the
// same way (decisions of 16–17.11.2026): both kinds, the main one or the bookings alone; the
// cancelled ones of either are switched on apart, off by itself; the location; the days of the
// start; put by the number of the order, the newest first, turned over from the heading.
type ListKind = "all" | "main" | "bookings";

function useListFilters() {
  const [kind, setKind] = useState<ListKind>("all");
  const [cancelled, setCancelled] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [desc, setDesc] = useState(true);
  // The search of the list (customer's decision of 17.11.2026): by what a row shows and by a tag.
  const [q, setQ] = useState("");
  return { kind, setKind, cancelled, setCancelled, locationId, setLocationId, from, setFrom, to, setTo, desc, setDesc, q, setQ };
}
type ListFilters = ReturnType<typeof useListFilters>;

/** Whether a row passes: what it is, whether it is cancelled, where and on which day it starts. */
function passes(f: ListFilters, row: { booking: boolean; cancelled: boolean; locationId: string | null; day: string }) {
  return (
    (f.kind === "all" || (f.kind === "bookings") === row.booking) &&
    (f.cancelled || !row.cancelled) &&
    (!f.locationId || row.locationId === f.locationId) &&
    (!f.from || row.day >= f.from) &&
    (!f.to || (row.day !== "" && row.day <= f.to))
  );
}

/** The day of a moment in a time zone, as the fields of the dates give it. */
function dayIn(moment: string | null, zone: string) {
  return moment ? new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(new Date(moment)) : "";
}

/** By the number of the order; inside one order by the order of creation — the identifiers
 * grow with time. */
function byOrder<T extends { id: string; order_id: string }>(rows: T[], numbers: Map<string, number>, desc: boolean) {
  return [...rows].sort((a, b) => {
    const [x, y] = [numbers.get(a.order_id) ?? 0, numbers.get(b.order_id) ?? 0];
    const order = x !== y ? x - y : a.id < b.id ? -1 : 1;
    return desc ? -order : order;
  });
}

function ListBar({ f, both, main }: { f: ListFilters; both: string; main: string }) {
  const { t } = useTranslation();
  const locations = useLocations().data?.locations ?? [];
  const kinds: [ListKind, string][] = [
    ["all", both],
    ["main", main],
    ["bookings", t("rentals.filter_bookings")],
  ];
  return (
    <div className="list-bar">
      <div className="segmented" role="group">
        {kinds.map(([key, label]) => (
          <button key={key} type="button" aria-pressed={f.kind === key} onClick={() => f.setKind(key)}>
            {label}
          </button>
        ))}
      </div>
      <Switch name={t("rentals.filter_cancelled")} checked={f.cancelled} onChange={f.setCancelled}>
        {t("rentals.filter_cancelled")}
      </Switch>
      {locations.length > 1 ? (
        <select value={f.locationId} aria-label={t("rentals.issue_location")} onChange={(e) => f.setLocationId(e.target.value)}>
          <option value="">{t("rentals.filter_all_locations")}</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      ) : null}
      <label className="inline-field">
        {t("rentals.filter_from")}
        <input type="date" value={f.from} max={f.to || undefined} onChange={(e) => f.setFrom(e.target.value)} />
      </label>
      <label className="inline-field">
        {t("rentals.filter_to")}
        <input type="date" value={f.to} min={f.from || undefined} onChange={(e) => f.setTo(e.target.value)} />
      </label>
      <SectionSearch compact value={f.q} onSearch={f.setQ} />
    </div>
  );
}

/** Whether the text of the search stands in one of the words of a row. */
function hasText(q: string, words: (string | number | null | undefined)[]) {
  const needle = q.trim().toLowerCase();
  return !needle || words.some((word) => word !== null && word !== undefined && String(word).toLowerCase().includes(needle));
}

/** The heading of the number of the order: a click turns the list over. */
function OrderHead({ f }: { f: ListFilters }) {
  const { t } = useTranslation();
  return (
    <th aria-sort={f.desc ? "descending" : "ascending"}>
      <button type="button" className="link" style={{ whiteSpace: "nowrap" }} onClick={() => f.setDesc(!f.desc)}>
        {t("orders.order")} {f.desc ? "↓" : "↑"}
      </button>
    </th>
  );
}

function OrderCell({ id, numbers }: { id: string; numbers: Map<string, number> }) {
  const company = useCompany();
  return <td>{numbers.has(id) ? <Link to={companyPath(company, `/orders/${id}`)}>№ {numbers.get(id)}</Link> : "—"}</td>;
}

type Tab = "schedule" | "rentals" | "trips" | "services" | "park" | "catalog";

/** The rental section (customer's decision of 11.11.2026): bookings and rentals; the ordered
 * extra services, linked to rentals and standalone; the tariffs and the catalog of services,
 * where they are created and changed. The actions stand in a column on the right. */
export function RentalsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [params, setParams] = useSearchParams();
  // The schedule and the trips are tabs of the rental (customer's decision of 14.11.2026). The
  // order is the customer's of 17.11.2026: rentals and bookings, the schedule, the ordered
  // services, the trips, the park, the tariffs and services; a user puts their own order
  // (SectionTabs), and the section opens on the first tab of that order.
  const TABS: Tab[] = ["rentals", "schedule", "services", ...(mayTrips(can) ? (["trips"] as Tab[]) : []), "park", "catalog"];
  const firstTab = useFirstTab("rentals");
  const tab: Tab = TABS.find((key) => key === params.get("tab")) ?? firstTab(TABS) ?? "rentals";
  const lines = useApi<{ lines: ServiceLine[] }>(
    ["service-lines", company.public_id],
    `/c/${company.public_id}/service-lines`,
    can("order.view") || can("rental.view"),
  );
  const booking: [boolean, string, string] = [can("rental.book"), "/rentals/book", t("vehicles.action_booking")];
  const actions: [boolean, string, string][] =
    tab === "rentals" || tab === "schedule"
      ? [[can("rental.issue"), "/rentals/new", t("vehicles.action_rental")], booking]
      : tab === "trips"
        ? []
      : tab === "park"
        ? [[can("vehicle.create"), "/vehicles/new?relation=own", t("vehicles.create_button")]]
      : tab === "services"
        ? [
            [can("order.create"), "/rentals/services/order?mode=now", t("rentals.action_service_now")],
            [can("order.create"), "/rentals/services/order", t("rentals.action_service")],
          ]
        : [
            [can("tariff.create"), "/rentals/tariffs/new", t("rentals.action_tariff")],
            [can("tariff.create"), "/rentals/services/new", t("rentals.action_catalog_service")],
          ];
  const allowed = actions.filter(([may]) => may);
  return (
    <div className="page full">
      <h1 className="sr-only">{t("nav.rentals")}</h1>
      <div className={allowed.length ? "split" : ""}>
        <div>
          <SectionTabs
            section="rentals"
            current={tab}
            tabs={TABS.map((key) => ({ key, text: t(`rentals.tab_${key}`) }))}
            onSelect={(key) => setParams({ tab: key })}
          />
          {tab === "schedule" ? <ScheduleTab /> : null}
          {tab === "trips" ? <TripsView /> : null}
          {tab === "rentals" ? <RentalsList lines={lines.data?.lines ?? []} /> : null}
          {tab === "services" ? <OrderedServices lines={lines.data?.lines ?? []} loading={lines.isLoading} /> : null}
          {tab === "park" ? <Park /> : null}
          {tab === "catalog" ? <Catalog /> : null}
        </div>
        {allowed.length ? (
          <aside className="side-actions" aria-label={t("vehicles.actions_title")}>
            {allowed.map(([, path, label]) => (
              <Link key={path} className="button secondary" to={companyPath(company, path)}>
                <Icon name="plus" size={16} /> {label}
              </Link>
            ))}
          </aside>
        ) : null}
      </div>
    </div>
  );
}

/** Ordered extra services: each is a record of its own, also when it belongs to a rental — then
 * the row leads to that rental. */
function OrderedServices({ lines, loading }: { lines: ServiceLine[]; loading: boolean }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const rentals = useRentals(useCan()("rental.view"));
  const vehicles = useVehicles();
  const vehicleName = new Map((vehicles.data?.vehicles ?? []).map((v) => [v.id, v.name]));
  const rentalVehicle = new Map((rentals.data?.rentals ?? []).map((r) => [r.id, vehicleName.get(r.vehicle_card_id) ?? ""]));
  // Filtered as the rentals are (decision of 17.11.2026). A service not started yet is a
  // booking, and stays one when it is cancelled before its start.
  const f = useListFilters();
  const orders = useOrders();
  const orderNumber = new Map((orders.data?.orders ?? []).map((o) => [o.id, o.number]));
  const all = lines;
  lines = byOrder(
    all.filter(
      (line) =>
        passes(f, {
          booking: line.state === "planned" || (line.state === "cancelled" && !line.actual_start),
          cancelled: line.state === "cancelled",
          locationId: line.location_id,
          day: dayIn(line.actual_start ?? line.period?.start ?? null, line.time_zone),
        }) &&
        hasText(f.q, [
          line.name,
          line.equipment?.public_id,
          orderNumber.get(line.order_id),
          line.rental_id ? rentalVehicle.get(line.rental_id) : "",
        ]),
    ),
    orderNumber,
    f.desc,
  );
  return (
    <Card>
      <ListBar f={f} both={t("rentals.filter_services_both")} main={t("rentals.filter_services")} />
      {loading ? <Loading /> : null}
      {!loading && all.length === 0 ? <p className="muted">{t("services.no_lines")}</p> : null}
      {all.length > 0 && lines.length === 0 ? <p className="muted">{t("rentals.none_found")}</p> : null}
      {lines.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <OrderHead f={f} />
                <th>{t("rentals.service_name")}</th>
                <th>{t("rentals.start")}</th>
                <th>{t("rentals.amount")}</th>
                <th>{t("rentals.service_rental")}</th>
                <th>{t("rentals.state")}</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.id}>
                  <OrderCell id={line.order_id} numbers={orderNumber} />
                  <td>
                    <Link
                      to={companyPath(
                        company,
                        line.rental_id ? `/rentals/${line.rental_id}#service-${line.id}` : `/rentals/service-lines/${line.id}`,
                      )}
                    >
                      {line.name}
                    </Link>
                    {line.equipment ? <span className="muted small"> · {line.equipment.public_id}</span> : null}
                  </td>
                  <td>
                    {/* The actual start when there is one; else the start of the booked period. */}
                    {(line.actual_start ?? line.period?.start)
                      ? formatMoment(line.actual_start ?? line.period?.start ?? null, line.time_zone, i18n.language)
                      : "—"}
                  </td>
                  <td>{line.calculation ? `${line.calculation.amount} ${line.calculation.currency}` : "—"}</td>
                  <td>
                    {line.rental_id ? (
                      <Link to={companyPath(company, `/rentals/${line.rental_id}`)}>
                        {rentalVehicle.get(line.rental_id) || t("rentals.service_rental_open")}
                      </Link>
                    ) : (
                      <span className="muted">{t("rentals.service_standalone")}</span>
                    )}
                  </td>
                  <td>{t(`services.state_${line.state}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}

/** Tariffs and the catalog of extra services: each opens its own page, where it is changed;
 * new ones are created from the buttons of the column. */
function Catalog() {
  const { t } = useTranslation();
  const company = useCompany();
  const tariffs = useTariffs();
  const services = useApi<{ services: ExtraService[] }>(
    ["extra-services", company.public_id],
    `/c/${company.public_id}/extra-services`,
  );
  return (
    <>
      <Card title={t("settings.tariffs")}>
        {tariffs.isLoading ? <Loading /> : null}
        {tariffs.data?.tariffs.length === 0 ? <p className="muted">{t("tariffs.none")}</p> : null}
        {(tariffs.data?.tariffs ?? []).map((tariff) => (
          <Link key={tariff.id} className="list-link" to={companyPath(company, `/rentals/tariffs/${tariff.id}`)}>
            <strong>{tariff.name}</strong>{" "}
            {tariff.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
            <div className="muted small">
              {unitText(tariff, t)} · {tariff.currency} · {rangesText(tariff.ranges, t, tariff.unit)}
            </div>
          </Link>
        ))}
      </Card>
      <Card title={t("services.catalog")}>
        {services.isLoading ? <Loading /> : null}
        {services.data?.services.length === 0 ? <p className="muted">{t("services.none")}</p> : null}
        {(services.data?.services ?? []).map((service) => (
          <Link key={service.id} className="list-link" to={companyPath(company, `/rentals/services/${service.id}`)}>
            <strong>{service.name}</strong>{" "}
            {service.state === "archived" ? <span className="badge">{t("tariffs.archived")}</span> : null}
            <div className="muted small">{serviceTerms(service, t)}</div>
          </Link>
        ))}
      </Card>
    </>
  );
}

/** Bookings and rentals in one list with a filter by kind; locations are columns of the list
 * (decision of 24.10.2026). The amount is the charged total of the order of the rental; the
 * extra services ordered with a rental are counted in its row (decision of 11.11.2026). */
function RentalsList({ lines }: { lines: ServiceLine[] }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const rentals = useRentals(can("rental.view"));
  const vehicles = useVehicles();
  const locations = useLocations();
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, ""],
    `/c/${company.public_id}/clients?q=`,
    can("client.list"),
  );
  const vehicleName = new Map((vehicles.data?.vehicles ?? []).map((v) => [v.id, v.name]));
  const clientNames = new Map((clients.data?.clients ?? []).map((c) => [c.id, clientName(c)]));
  const zones = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.timezone ?? "UTC"]));
  const places = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.name]));
  // § 14.3.7, decision В7: exceptions recorded after the transfer wait for a decision.
  const [waiting, setWaiting] = useState(false);
  const all = rentals.data?.rentals ?? [];
  const pending = all.filter((r) => r.issue_exception?.state === "pending");
  // What is listed (decision of 16.11.2026): rentals and bookings, rentals alone or bookings
  // alone; the cancelled ones of either are switched on apart, off by itself.
  const f = useListFilters();
  // A rental created ahead of its issue is a rental, not a booking (decision of 15.11.2026).
  const awaiting = (r: { state: string; booking?: Booking | null }) => r.state === "booked" && Boolean(r.booking?.direct);
  // A booking stays a booking when it is cancelled; the same for a rental.
  const isBooking = (r: { state: string; booking?: Booking | null }) =>
    r.state === "proposed" || (["booked", "cancelled"].includes(r.state) && Boolean(r.booking) && !r.booking?.direct);
  const startOf = (r: (typeof all)[number]) => r.actual_start ?? r.booking?.planned_start ?? null;
  const zoneOf = (r: (typeof all)[number]) => zones.get(r.issue_location_id ?? "") ?? company.timezone ?? "UTC";
  const orders = useOrders();
  const orderNumber = new Map((orders.data?.orders ?? []).map((o) => [o.id, o.number]));
  const tagged = useTagMatch("rental");
  const shown = byOrder(
    (waiting ? pending : all).filter(
      (r) =>
        passes(f, {
          booking: isBooking(r),
          cancelled: ["cancelled", "annulled"].includes(r.state),
          locationId: r.issue_location_id,
          day: dayIn(startOf(r), zoneOf(r)),
        }) &&
        (hasText(f.q, [
          vehicleName.get(r.vehicle_card_id),
          r.client_id ? clientNames.get(r.client_id) : "",
          orderNumber.get(r.order_id),
          r.booking?.number,
        ]) ||
          tagged(r.id, f.q)),
    ),
    orderNumber,
    f.desc,
  );
  const amounts = new Map(
    (orders.data?.orders ?? []).map((o) => [
      o.id,
      Object.entries(o.amounts)
        .map(([currency, a]) => `${a.charged} ${currency}`)
        .join(", "),
    ]),
  );
  const services = new Map<string, number>();
  for (const line of lines) {
    if (line.rental_id && line.state !== "cancelled") services.set(line.rental_id, (services.get(line.rental_id) ?? 0) + 1);
  }
  return (
    <>
      <Card>
        <ListBar f={f} both={t("rentals.filter_both")} main={t("rentals.filter_rentals")} />
        {pending.length || waiting ? (
          <label className="small">
            <input type="checkbox" checked={waiting} onChange={() => setWaiting(!waiting)} />{" "}
            {t("issue_rules.filter_pending", { count: pending.length })}
          </label>
        ) : null}
        {rentals.isLoading ? <Loading /> : null}
        {rentals.data?.rentals.length === 0 ? <p className="muted">{t("rentals.none")}</p> : null}
        {all.length > 0 && shown.length === 0 ? <p className="muted">{t("rentals.none_found")}</p> : null}
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <OrderHead f={f} />
              <th>{t("rentals.vehicle")}</th>
              <th>{t("rentals.client")}</th>
              <th>{t("rentals.start")}</th>
              <th>{t("rentals.issue_location")}</th>
              <th>{t("rentals.end")}</th>
              <th>{t("rentals.return_location")}</th>
              <th>{t("rentals.amount")}</th>
              <th>{t("rentals.state")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((rental) => {
              const zone = zoneOf(rental);
              const booking = rental.booking;
              // A booking is shown with its planned period until the issue (§ 14.1).
              const start = rental.actual_start ?? booking?.planned_start ?? null;
              const end = rental.actual_end ?? (rental.state === "booked" ? rental.planned_end : null);
              return (
                <tr key={rental.id}>
                  <OrderCell id={rental.order_id} numbers={orderNumber} />
                  <td>
                    <Link to={companyPath(company, `/rentals/${rental.id}`)}>
                      {vehicleName.get(rental.vehicle_card_id) ?? "—"}
                    </Link>
                    {booking && !booking.direct ? (
                      <div className="muted small">{t("bookings.number", { number: booking.number })}</div>
                    ) : null}
                  </td>
                  <td>
                    {rental.client_id ? (clientNames.get(rental.client_id) ?? "—") : null}
                    {booking?.client_missing && rental.state === "booked" ? (
                      <span className="badge">{t("bookings.client_missing_badge")}</span>
                    ) : null}
                  </td>
                  <td>{formatMoment(start, zone, i18n.language)}</td>
                  <td>{places.get(rental.issue_location_id ?? "") ?? "—"}</td>
                  <td>{formatMoment(end, zone, i18n.language)}</td>
                  <td>
                    {rental.return_location_id ? (places.get(rental.return_location_id) ?? "—") : "—"}
                  </td>
                  <td>{amounts.get(rental.order_id) || "—"}</td>
                  <td>
                    {t(awaiting(rental) ? "rentals.state_awaiting" : `rentals.state_${rental.state}`)}
                    {services.get(rental.id) ? (
                      <span className="badge"> {t("rentals.services_badge", { count: services.get(rental.id) })}</span>
                    ) : null}
                    {booking?.delay_conflict ? <span className="badge"> {t("bookings.delay_badge")}</span> : null}
                    {rental.issue_exception?.state === "pending" || rental.issue_exception?.state === "rejected" ? (
                      <span className="badge"> {t(`issue_rules.badge_${rental.issue_exception.state}`)}</span>
                    ) : null}
                    {/* § 14.3.3: a missing deposit is shown apart from the debt. */}
                    {rental.deposit?.conflict ? (
                      <span className="badge">
                        {" "}
                        {t("deposit.missing_badge", { amount: rental.deposit.missing, currency: rental.deposit.currency })}
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </Card>
    </>
  );
}
