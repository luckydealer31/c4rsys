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

/** Rentals of the company with the action «Issue a vehicle» (STAGE1_SCREENS). */
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
  return (
    <div className="page">
      <h1>{t("nav.rentals")}</h1>
      {can("rental.issue") ? (
        <div className="actions" style={{ marginBottom: 16, marginTop: 0 }}>
          <Link className="button" to={companyPath(company, "/rentals/new")}>
            {t("rentals.issue")}
          </Link>
        </div>
      ) : null}
      <Card>
        {rentals.isLoading ? <Loading /> : null}
        {rentals.data?.rentals.length === 0 ? <p className="muted">{t("rentals.none")}</p> : null}
        <table>
          <thead>
            <tr>
              <th>{t("rentals.vehicle")}</th>
              <th>{t("rentals.client")}</th>
              <th>{t("rentals.start")}</th>
              <th>{t("rentals.end")}</th>
              <th>{t("rentals.state")}</th>
            </tr>
          </thead>
          <tbody>
            {(rentals.data?.rentals ?? []).map((rental) => {
              const zone = zones.get(rental.issue_location_id) ?? "UTC";
              return (
                <tr key={rental.id}>
                  <td>
                    <Link to={companyPath(company, `/rentals/${rental.id}`)}>
                      {vehicleName.get(rental.vehicle_card_id) ?? "—"}
                    </Link>
                  </td>
                  <td>{clientNames.get(rental.client_id) ?? "—"}</td>
                  <td>{formatMoment(rental.actual_start, zone, i18n.language)}</td>
                  <td>{formatMoment(rental.actual_end, zone, i18n.language)}</td>
                  <td>{t(`rentals.state_${rental.state}`)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
