/** Inspections of the company with filters by vehicle, state and kind (ТЗ гл. 10). */
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import type { ApiError } from "../../api/client";
import type { Inspection } from "../../api/types";
import { ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { useScope } from "../../offline/hooks";
import { KINDS, STATES, stateBadge, useInspections } from "./data";
import { DraftsPanel } from "./OfflineDrafts";

export function InspectionsPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const scope = useScope();
  const [params, setParams] = useSearchParams();
  const vehicles = useVehicles();
  const vehicle = params.get("vehicle") ?? "";
  const state = params.get("state") ?? "";
  const kind = params.get("kind") ?? "";
  const query = new URLSearchParams({
    ...(vehicle ? { vehicle_card_id: vehicle } : {}),
    ...(state ? { state } : {}),
  }).toString();
  const list = useInspections(query);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  // The kind is filtered here: the list answers at most 200 latest inspections.
  const items = (list.data?.inspections ?? []).filter((i) => !kind || i.kind === kind);
  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("inspections.title")}</h1>
        {can("inspection.create") ? (
          <Link className="button" to={companyPath(company, `/inspections/new${vehicle ? `?vehicle=${vehicle}` : ""}`)}>
            {t("inspections.new")}
          </Link>
        ) : null}
      </div>
      {/* Local drafts of this account in this company (§ 10.4.2–10.4.4). */}
      <DraftsPanel
        scope={scope}
        base={companyPath(company)}
        zone={company.timezone ?? "UTC"}
        canCreate={can("inspection.create")}
        always={can("inspection.create")}
      />
      <div className="row filters">
        <Field label={t("inspections.vehicle")}>
          <select value={vehicle} onChange={(e) => set("vehicle", e.target.value)}>
            <option value="">{t("inspections.all")}</option>
            {(vehicles.data?.vehicles ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} · {v.vehicle_id}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("inspections.state")}>
          <select value={state} onChange={(e) => set("state", e.target.value)}>
            <option value="">{t("inspections.all")}</option>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {t(`inspections.state_${s}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("inspections.kind")}>
          <select value={kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">{t("inspections.all")}</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`inspections.kind_${k}`)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {list.isLoading ? <Loading /> : null}
      <ErrorMessage error={list.error as ApiError} />
      {!list.isLoading && items.length === 0 ? <p className="muted">{t("inspections.none")}</p> : null}
      <InspectionList items={items} showVehicle />
    </div>
  );
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
