/** Inspections: the shared list of cards; the old address of the company list (ТЗ гл. 10). */
import { useTranslation } from "react-i18next";
import { Link, Navigate, useSearchParams } from "react-router";

import type { Inspection } from "../../api/types";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { stateBadge } from "./data";

/** There is no list of all inspections of the company (customer's decision of 11.11.2026): an
 * inspection is found where it belongs — in the card of its vehicle, in its rental or service
 * operation. The old address leads to the inspections of the vehicle it names, else to the
 * garage, where local drafts of this device are shown while there are any. */
export function InspectionsPage() {
  const company = useCompany();
  const [params] = useSearchParams();
  const vehicle = params.get("vehicle");
  return <Navigate to={companyPath(company, vehicle ? `/vehicles/${vehicle}?tab=inspections` : "/vehicles")} replace />;
}

/** Inspections as a list of cards that reads well on a phone. */
export function InspectionList({ items, showVehicle }: { items: Inspection[]; showVehicle?: boolean }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const vehicles = useVehicles();
  const locations = useLocations();
  const zoneOf = (item: Inspection) => {
    const vehicle = vehicles.data?.vehicles.find((v) => v.id === item.vehicle_card_id);
    const id = item.place.location_id ?? vehicle?.location_id;
    return locations.data?.locations.find((l) => l.id === id)?.timezone ?? company.timezone ?? "UTC";
  };
  return (
    <div className="inspection-list">
      {items.map((item) => {
        const vehicle = vehicles.data?.vehicles.find((v) => v.id === item.vehicle_card_id);
        const photos = item.files.filter((f) => f.kind === "photo").length;
        return (
          <Link key={item.id} className="list-link" to={companyPath(company, `/inspections/${item.id}`)}>
            <div className="list-link-head">
              <strong>{t(`inspections.kind_${item.kind}`)}</strong>
              <span className={`badge ${stateBadge(item.state)}`}>{t(`inspections.state_${item.state}`)}</span>
            </div>
            <div className="muted small">
              {formatMoment(item.state === "planned" ? item.planned_at : item.performed_at, zoneOf(item), i18n.language)}
              {" · "}
              {t(`inspections.application_${item.application}`)}
              {showVehicle && vehicle ? ` · ${vehicle.name}` : ""}
              {item.state === "saved" ? ` · ${t(`inspections.check_${item.check_status}`)}` : ""}
              {photos ? ` · ${t("inspections.photo_count", { count: photos })}` : ""}
            </div>
          </Link>
        );
      })}
    </div>
  );
}
