import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import type { Client, ExtraService, ServiceLine } from "../../api/types";
import { useApi } from "../../api/queries";
import { Icon } from "../../components/icons";
import { Card, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useOrders } from "../orders/OrdersPage";
import { useLocations } from "../settings/LocationsPage";
import { rangesText, unitText, useTariffs } from "../tariffs/TariffsPage";
import { serviceTerms } from "./Services";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";

// Three switches of the list, all on at first; each takes its records out of the list or
// back into it (customer's decision of 11.11.2026). «Cancelled» is one switch for cancelled
// bookings and annulled rentals alike (PROPOSAL).
type Kind = "rentals" | "bookings" | "cancelled";
const KINDS: Record<Kind, string[]> = {
  rentals: ["active", "returned"],
  bookings: ["booked", "proposed"],
  cancelled: ["cancelled", "annulled"],
};
const ALL_KINDS = Object.keys(KINDS) as Kind[];

type Tab = "rentals" | "services" | "catalog";
const TABS: Tab[] = ["rentals", "services", "catalog"];

/** The rental section (customer's decision of 11.11.2026): bookings and rentals; the ordered
 * extra services, linked to rentals and standalone; the tariffs and the catalog of services,
 * where they are created and changed. The actions stand in a column on the right. */
export function RentalsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.find((key) => key === params.get("tab")) ?? "rentals";
  const lines = useApi<{ lines: ServiceLine[] }>(
    ["service-lines", company.public_id],
    `/c/${company.public_id}/service-lines`,
    can("order.view") || can("rental.view"),
  );
  const booking: [boolean, string, string] = [can("rental.book"), "/rentals/book", t("vehicles.action_booking")];
  const actions: [boolean, string, string][] =
    tab === "rentals"
      ? [[can("rental.issue"), "/rentals/new", t("vehicles.action_rental")], booking]
      : tab === "services"
        ? [[can("order.create"), "/rentals/services/order", t("rentals.action_service")]]
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
          <div className="tabs left" role="tablist">
            {TABS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                className={tab === key ? "active" : ""}
                onClick={() => setParams(key === "rentals" ? {} : { tab: key })}
              >
                {t(`rentals.tab_${key}`)}
              </button>
            ))}
          </div>
          {tab === "rentals" ? <RentalsList lines={lines.data?.lines ?? []} /> : null}
          {tab === "services" ? <OrderedServices lines={lines.data?.lines ?? []} loading={lines.isLoading} /> : null}
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
  return (
    <Card>
      {loading ? <Loading /> : null}
      {!loading && lines.length === 0 ? <p className="muted">{t("services.no_lines")}</p> : null}
      {lines.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
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
                  <td>
                    <Link
                      to={companyPath(
                        company,
                        line.rental_id ? `/rentals/${line.rental_id}#service-${line.id}` : `/rentals/service-lines/${line.id}`,
                      )}
                    >
                      {line.name}
                    </Link>
                  </td>
                  <td>{line.period ? formatMoment(line.period.start, line.time_zone, i18n.language) : "—"}</td>
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
  const [kinds, setKinds] = useState<Kind[]>(ALL_KINDS);
  const states = new Set(kinds.flatMap((key) => KINDS[key]));
  const known = new Set(ALL_KINDS.flatMap((key) => KINDS[key]));
  // A state outside the three groups is never hidden by them.
  const shown = (waiting ? pending : all).filter((r) => states.has(r.state) || !known.has(r.state));
  // The last switch that is on stays on.
  const toggleKind = (key: Kind) =>
    setKinds(kinds.includes(key) ? (kinds.length > 1 ? kinds.filter((k) => k !== key) : kinds) : [...kinds, key]);
  const orders = useOrders();
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
        <div className="list-bar">
          {ALL_KINDS.map((key) => (
            <button
              key={key}
              type="button"
              className="toggle-chip"
              aria-pressed={kinds.includes(key)}
              onClick={() => toggleKind(key)}
            >
              {t(`rentals.filter_${key}`)}
            </button>
          ))}
          {/* «All» turns the three back on (decision of 11.11.2026). */}
          <button
            type="button"
            className="toggle-chip"
            aria-pressed={kinds.length === ALL_KINDS.length}
            onClick={() => setKinds(ALL_KINDS)}
          >
            {t("rentals.filter_all")}
          </button>
        </div>
        {pending.length || waiting ? (
          <label className="small">
            <input type="checkbox" checked={waiting} onChange={() => setWaiting(!waiting)} />{" "}
            {t("issue_rules.filter_pending", { count: pending.length })}
          </label>
        ) : null}
        {rentals.isLoading ? <Loading /> : null}
        {rentals.data?.rentals.length === 0 ? <p className="muted">{t("rentals.none")}</p> : null}
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
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
              const zone = zones.get(rental.issue_location_id ?? "") ?? company.timezone ?? "UTC";
              const booking = rental.booking;
              // A booking is shown with its planned period until the issue (§ 14.1).
              const start = rental.actual_start ?? booking?.planned_start ?? null;
              const end = rental.actual_end ?? (rental.state === "booked" ? rental.planned_end : null);
              return (
                <tr key={rental.id}>
                  <td>
                    <Link to={companyPath(company, `/rentals/${rental.id}`)}>
                      {vehicleName.get(rental.vehicle_card_id) ?? "—"}
                    </Link>
                    {booking ? <div className="muted small">{t("bookings.number", { number: booking.number })}</div> : null}
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
                    {t(`rentals.state_${rental.state}`)}
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
