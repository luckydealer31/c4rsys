/** A new inspection (§ 10.1): its kind and application — a rental, a service operation or on its
 * own — the vehicle and its operation, the place, the second participant, the performer and the
 * actual moment, by default now and never in the future. A planned one gets its planned moment
 * instead and is started later (§ 10.6.3). */
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { Inspection, InspectionApplication, InspectionKind } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput, toLocalInput } from "../../time";
import { useServiceOperations } from "../service/hooks";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";
import { APPLICATIONS, KINDS_OF, useRefreshInspections, useTemplates } from "./data";
import { DetailsFields, detailsBody, emptyDetails, type Details } from "./Fields";
import { useInspectionZone } from "./InspectionPage";

export function NewInspectionPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefreshInspections();
  const [params] = useSearchParams();
  const vehicles = useVehicles();
  const templates = useTemplates();
  const [vehicleId, setVehicleId] = useState(params.get("vehicle") ?? "");
  const [application, setApplication] = useState<InspectionApplication>(
    (params.get("application") as InspectionApplication | null) ??
      (params.get("rental") ? "rental" : params.get("service") ? "service" : "standalone"),
  );
  const [rentalId, setRentalId] = useState(params.get("rental") ?? "");
  const [serviceId, setServiceId] = useState(params.get("service") ?? "");
  const [kind, setKind] = useState<InspectionKind | "">((params.get("kind") as InspectionKind | null) ?? "");
  const [templateId, setTemplateId] = useState("");
  const [planned, setPlanned] = useState(false);
  const [plannedAt, setPlannedAt] = useState("");
  const [details, setDetails] = useState<Details>(() => emptyDetails(null));
  const rentals = useRentals(can("rental.view"));
  const operations = useServiceOperations("", can("service.view"));
  const create = useCommand<Record<string, unknown>, { inspection: Inspection }>(`/c/${company.public_id}/inspections`);

  // A rental or a service operation may also be given first; its vehicle follows.
  const linkedRental = (rentals.data?.rentals ?? []).find((r) => r.id === rentalId);
  const linkedOperation = (operations.data?.operations ?? []).find((o) => o.id === serviceId);
  const effectiveVehicle = vehicleId || linkedRental?.vehicle_card_id || linkedOperation?.vehicle_card_id || "";
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === effectiveVehicle);
  const zone = useInspectionZone(null, vehicle);
  const ownRentals = (rentals.data?.rentals ?? []).filter(
    (r) => r.vehicle_card_id === effectiveVehicle && r.state !== "cancelled",
  );
  const ownOperations = (operations.data?.operations ?? []).filter(
    (o) => o.vehicle_card_id === effectiveVehicle && !["cancelled", "terminated"].includes(o.state),
  );
  const kinds = KINDS_OF[application];
  const clientId =
    (application === "rental" ? linkedRental?.client_id : application === "service" ? linkedOperation?.client_id : null) ??
    vehicle?.owner?.client?.id ??
    null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await create.run({
      vehicle_card_id: effectiveVehicle,
      kind,
      application,
      rental_id: application === "rental" ? rentalId || null : null,
      service_operation_id: application === "service" ? serviceId || null : null,
      template_id: templateId || null,
      planned_at: planned && plannedAt ? fromLocalInput(plannedAt, zone) : null,
      ...detailsBody(planned ? { ...details, performed_at: "" } : details, zone),
    });
    if (result) {
      await refresh();
      navigate(companyPath(company, `/inspections/${result.inspection.id}`), { replace: true });
    }
  }

  const blocked =
    !effectiveVehicle ||
    !kind ||
    (application === "rental" && !rentalId) ||
    (application === "service" && !serviceId) ||
    (planned && !plannedAt);

  return (
    <div className="page inspection-page">
      <p>
        <Link to={companyPath(company, "/inspections")}>← {t("inspections.title")}</Link>
      </p>
      <h1>{t("inspections.new")}</h1>
      <form onSubmit={submit}>
        <Card>
          <div className="stack">
            <Field label={t("inspections.vehicle")}>
              <select value={effectiveVehicle} onChange={(e) => setVehicleId(e.target.value)}>
                <option value="">{t("inspections.choose")}</option>
                {(vehicles.data?.vehicles ?? []).map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} · {v.vehicle_id}
                    {v.characteristics.plate ? ` · ${v.characteristics.plate}` : ""}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("inspections.application")}>
              <select
                value={application}
                onChange={(e) => {
                  const next = e.target.value as InspectionApplication;
                  setApplication(next);
                  if (kind && !KINDS_OF[next].includes(kind)) setKind("");
                }}
              >
                {APPLICATIONS.map((a) => (
                  <option key={a} value={a}>
                    {t(`inspections.application_${a}`)}
                  </option>
                ))}
              </select>
            </Field>
            {application === "rental" ? (
              <Field label={t("inspections.rental")}>
                <select value={rentalId} onChange={(e) => setRentalId(e.target.value)}>
                  <option value="">{t("inspections.choose")}</option>
                  {ownRentals.map((r) => (
                    <option key={r.id} value={r.id}>
                      {t(`rentals.state_${r.state}`)} · {r.actual_start ? r.actual_start.slice(0, 10) : "—"}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            {application === "service" ? (
              <Field label={t("inspections.service_operation")}>
                <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
                  <option value="">{t("inspections.choose")}</option>
                  {ownOperations.map((o) => (
                    <option key={o.id} value={o.id}>
                      {t("service.operation_title", { number: o.number })} · {t(`service.state_${o.state}`)}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
            <Field label={t("inspections.kind")}>
              <select value={kind} onChange={(e) => setKind(e.target.value as InspectionKind)}>
                <option value="">{t("inspections.choose")}</option>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {t(`inspections.kind_${k}`)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("inspections.template")} hint={t("inspections.template_hint")}>
              <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                <option value="">{t("inspections.template_auto")}</option>
                {(templates.data?.templates ?? []).map((tpl) => (
                  <option key={tpl.id} value={tpl.id}>
                    {t(`inspections.transport_${tpl.transport}`)} · v{tpl.version}
                  </option>
                ))}
              </select>
            </Field>
            <label>
              <input type="checkbox" checked={planned} onChange={() => setPlanned(!planned)} /> {t("inspections.plan_later")}
            </label>
            {planned ? (
              <Field label={t("inspections.planned_at")}>
                <input type="datetime-local" value={plannedAt} onChange={(e) => setPlannedAt(e.target.value)} />
              </Field>
            ) : null}
          </div>
        </Card>
        <Card title={t("inspections.details")}>
          <DetailsFields
            value={details}
            onChange={setDetails}
            zone={zone}
            initialMoment={new Date()}
            clientId={clientId}
            clientLabel={t("inspections.participant_client")}
            planned={planned}
          />
          {!planned ? <p className="muted small">{t("inspections.now_hint", { now: toLocalInput(new Date(), zone).replace("T", " ") })}</p> : null}
        </Card>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={create.pending || blocked}>
            {planned ? t("inspections.create_planned") : t("inspections.create")}
          </button>
        </div>
      </form>
    </div>
  );
}
