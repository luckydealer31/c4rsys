import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Client } from "../../api/types";
import { useApi } from "../../api/queries";
import { Card, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";

/** Rentals of the company with the action «Issue a vehicle» (STAGE1_SCREENS). Locations are
 * columns of the list, not a block of their own (decision of 24.10.2026). */
export function RentalsPage() {
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
  const shown = waiting ? pending : all;
  return (
    <div className="page">
      <h1>{t("nav.rentals")}</h1>
      {can("rental.issue") ? (
        <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
          <Link className="button" to={companyPath(company, "/rentals/new")}>
            {t("rentals.issue")}
          </Link>
          {can("rental.book") ? (
            <Link className="button secondary" to={companyPath(company, "/rentals/book")}>
              {t("bookings.new")}
            </Link>
          ) : null}
          {can("tariff.view") ? (
            <>
              <Link className="button secondary" to={companyPath(company, "/rentals/tariffs")}>
                {t("settings.tariffs")}
              </Link>
              <Link className="button secondary" to={companyPath(company, "/rentals/services")}>
                {t("services.catalog")}
              </Link>
            </>
          ) : null}
        </div>
      ) : can("tariff.view") || can("rental.book") ? (
        <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
          {can("rental.book") ? (
            <Link className="button secondary" to={companyPath(company, "/rentals/book")}>
              {t("bookings.new")}
            </Link>
          ) : null}
          {can("tariff.view") ? (
            <>
              <Link className="button secondary" to={companyPath(company, "/rentals/tariffs")}>
                {t("settings.tariffs")}
              </Link>
              <Link className="button secondary" to={companyPath(company, "/rentals/services")}>
                {t("services.catalog")}
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
      <Card>
        {pending.length || waiting ? (
          <label className="small">
            <input type="checkbox" checked={waiting} onChange={() => setWaiting(!waiting)} />{" "}
            {t("issue_rules.filter_pending", { count: pending.length })}
          </label>
        ) : null}
        {rentals.isLoading ? <Loading /> : null}
        {rentals.data?.rentals.length === 0 ? <p className="muted">{t("rentals.none")}</p> : null}
        <table>
          <thead>
            <tr>
              <th>{t("rentals.vehicle")}</th>
              <th>{t("rentals.client")}</th>
              <th>{t("rentals.start")}</th>
              <th>{t("rentals.issue_location")}</th>
              <th>{t("rentals.end")}</th>
              <th>{t("rentals.return_location")}</th>
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
                  <td>
                    {t(`rentals.state_${rental.state}`)}
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
      </Card>
    </div>
  );
}
