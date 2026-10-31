/** Entry points of inspections and damages on the cards of a vehicle, a rental and a service
 * operation (ТЗ гл. 10, § 16.5.4). */
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Rental, ServiceOperation, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { useInspections, useRefreshInspections, useVehicleDamages } from "./data";
import { DamageList, damageText } from "./DamagesEditor";
import { amount, EstimateForm } from "./DamagePage";
import { InspectionList } from "./InspectionsPage";
import { PhotoInput, PhotoThumb } from "./Media";

function newPath(params: Record<string, string>): string {
  return `/inspections/new?${new URLSearchParams(params).toString()}`;
}

export function VehicleInspectionsCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const list = useInspections(`vehicle_card_id=${vehicle.id}`);
  const items = list.data?.inspections ?? [];
  return (
    <Card title={t("inspections.title")}>
      {list.isLoading ? <Loading /> : null}
      {!list.isLoading && items.length === 0 ? <p className="muted">{t("inspections.none")}</p> : null}
      <InspectionList items={items.slice(0, 10)} />
      <div className="actions">
        {can("inspection.create") && vehicle.state === "active" ? (
          <Link className="button" to={companyPath(company, newPath({ vehicle: vehicle.id }))}>
            {t("inspections.new")}
          </Link>
        ) : null}
        {items.length > 10 ? (
          <Link className="button secondary" to={companyPath(company, `/inspections?vehicle=${vehicle.id}`)}>
            {t("inspections.all_of_vehicle")}
          </Link>
        ) : null}
      </div>
    </Card>
  );
}

/** § 10.5.2: damages are events of the vehicle's history; created only by an inspection. */
export function VehicleDamagesCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const damages = useVehicleDamages(vehicle.id);
  const items = damages.data?.damages ?? [];
  const [all, setAll] = useState(false);
  const shown = all ? items : items.filter((d) => d.state === "actual");
  return (
    <Card title={t("inspections.damages")}>
      {damages.isLoading ? <Loading /> : null}
      <p className="muted small">{t("inspections.damages_only_by_inspection")}</p>
      {!damages.isLoading && shown.length === 0 ? <p className="muted">{t("inspections.no_damages")}</p> : null}
      <DamageList damages={shown} />
      {items.length > shown.length || all ? (
        <label className="small">
          <input type="checkbox" checked={all} onChange={() => setAll(!all)} /> {t("inspections.show_all_damages")}
        </label>
      ) : null}
    </Card>
  );
}

/** The inspections of a rental: start one of the issue or the return, the conflict of a required
 * check not done and its reason (§ 10.3.3), the damages charged in the rental (§ 10.5.3–10.5.5). */
export function RentalInspectionsCard({ rental }: { rental: Rental }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const list = useInspections(`rental_id=${rental.id}`);
  const damages = useVehicleDamages(rental.vehicle_card_id);
  const items = list.data?.inspections ?? [];
  const charged = (damages.data?.damages ?? []).filter((d) => d.rental_id === rental.id && d.attributed_to_rental);
  const closed = Boolean(rental.formalization.completed_at);
  const open = !closed && rental.state !== "cancelled";
  const start = (kind: string) =>
    companyPath(company, newPath({ vehicle: rental.vehicle_card_id, rental: rental.id, application: "rental", kind }));
  const { check_conflicts: conflicts, missing_reasons: reasons } = rental.inspections;
  return (
    <Card title={t("inspections.of_rental")}>
      {conflicts.map((kind) => (
        <p key={kind} className="alert warn">
          {t(`inspections.conflict_${kind}`)}
        </p>
      ))}
      {list.isLoading ? <Loading /> : null}
      {!list.isLoading && items.length === 0 ? <p className="muted">{t("inspections.none")}</p> : null}
      <InspectionList items={items} />
      {open && can("inspection.create") ? (
        <div className="actions">
          {rental.state === "booked" || rental.state === "active" ? (
            <Link className="button secondary" to={start("issue")}>
              {t("inspections.start_issue")}
            </Link>
          ) : null}
          {rental.state === "active" || rental.state === "returned" ? (
            <>
              <Link className="button secondary" to={start("return")}>
                {t("inspections.start_return")}
              </Link>
              <Link className="button secondary" to={start("intermediate")}>
                {t("inspections.start_intermediate")}
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
      {reasons.length ? (
        <>
          <h3 style={{ marginTop: 16 }}>{t("inspections.missing_reasons")}</h3>
          <ul>
            {reasons.map((r, index) => (
              <li key={index}>
                {t(`inspections.kind_${r.kind}`)}: {r.reason}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {conflicts.length && !closed && can("rental.complete_formalization") ? (
        <MissingReasonForm rental={rental} kinds={conflicts} />
      ) : null}
      {charged.length || !damages.isLoading ? (
        <>
          <h3 style={{ marginTop: 16 }}>{t("inspections.rental_damages")}</h3>
          {charged.length === 0 ? <p className="muted small">{t("inspections.no_rental_damages")}</p> : null}
          {charged.map((d) => (
            <div key={d.id} className="damage-entry">
              <DamageList damages={[d]} />
              <div className="muted small">
                {d.estimate
                  ? `${t("inspections.estimate")}: ${amount(d.estimate.amount)} ${d.estimate.currency}`
                  : t("inspections.not_estimated")}
                {d.charged !== null ? ` · ${t("inspections.charged")}: ${amount(d.charged)}` : ""}
              </div>
              {!closed && d.state !== "erroneous" && can("rental.manual_charge") ? <EstimateForm damage={d} compact /> : null}
            </div>
          ))}
        </>
      ) : null}
    </Card>
  );
}

function MissingReasonForm({ rental, kinds }: { rental: Rental; kinds: ("issue" | "return")[] }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshInspections();
  const [kind, setKind] = useState<string>(kinds[0] ?? "");
  const [reason, setReason] = useState("");
  const command = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/rentals/${rental.id}/inspection-reasons`,
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await command.run({ kind, reason: reason.trim() })) {
      setReason("");
      await refresh();
    }
  }
  return (
    <form onSubmit={submit} className="stack" style={{ marginTop: 12 }}>
      <h3>{t("inspections.record_reason")}</h3>
      <p className="muted small">{t("inspections.record_reason_hint")}</p>
      {kinds.length > 1 ? (
        <Field label={t("inspections.kind")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {t(`inspections.kind_${k}`)}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      <Field label={t("inspections.reason")}>
        <textarea rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={command.error} />
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="submit" disabled={command.pending || !reason.trim() || !kind}>
          {t("inspections.save_reason")}
        </button>
      </div>
    </form>
  );
}

/** Inspections of a service operation: at the acceptance into the service (`return`, § 10.1.1)
 * and intermediate ones. */
export function ServiceInspectionsCard({ op }: { op: ServiceOperation }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const list = useInspections(`service_operation_id=${op.id}`);
  const items = list.data?.inspections ?? [];
  const final = ["works_done", "cancelled", "terminated"].includes(op.state);
  const start = (kind: string) =>
    companyPath(company, newPath({ vehicle: op.vehicle_card_id, service: op.id, application: "service", kind }));
  return (
    <Card title={t("inspections.title")}>
      {list.isLoading ? <Loading /> : null}
      {!list.isLoading && items.length === 0 ? <p className="muted">{t("inspections.none")}</p> : null}
      <InspectionList items={items} />
      {!final && can("inspection.create") ? (
        <div className="actions">
          <Link className="button secondary" to={start("return")}>
            {t("inspections.start_intake")}
          </Link>
          <Link className="button secondary" to={start("intermediate")}>
            {t("inspections.start_intermediate")}
          </Link>
        </div>
      ) : null}
    </Card>
  );
}

export type FixedChoice = { damage_id: string; photo_file_ids: string[] };

/** § 16.5.4: at the completion of a work the employee marks explicitly which damages are fixed,
 * with photos of the result; the completion alone fixes nothing. */
export function FixedDamagesPicker({
  vehicleCardId,
  value,
  onChange,
}: {
  vehicleCardId: string;
  value: FixedChoice[];
  /** An update of the previous choice: uploads finish one after another. */
  onChange: (update: (previous: FixedChoice[]) => FixedChoice[]) => void;
}) {
  const { t } = useTranslation();
  const damages = useVehicleDamages(vehicleCardId);
  const actual = (damages.data?.damages ?? []).filter((d) => d.state === "actual");
  if (damages.isLoading || damages.error || actual.length === 0) return null;
  const chosen = (id: string) => value.find((v) => v.damage_id === id);
  return (
    <div className="stack" style={{ flexBasis: "100%" }}>
      <strong>{t("inspections.fixed_by_work")}</strong>
      <p className="muted small">{t("inspections.fixed_by_work_hint")}</p>
      {actual.map((d) => {
        const item = chosen(d.id);
        return (
          <div key={d.id} className="damage-entry">
            <label>
              <input
                type="checkbox"
                checked={Boolean(item)}
                onChange={() =>
                  onChange((previous) =>
                    item
                      ? previous.filter((v) => v.damage_id !== d.id)
                      : [...previous, { damage_id: d.id, photo_file_ids: [] }],
                  )
                }
              />{" "}
              {damageText(d) || t("inspections.damage_on_scheme")}
            </label>
            {item ? (
              <>
                <div className="media-row">
                  {item.photo_file_ids.map((f) => (
                    <PhotoThumb
                      key={f}
                      fileId={f}
                      size={72}
                      onRemove={() =>
                        onChange((previous) =>
                          previous.map((v) =>
                            v.damage_id === d.id ? { ...v, photo_file_ids: v.photo_file_ids.filter((p) => p !== f) } : v,
                          ),
                        )
                      }
                    />
                  ))}
                </div>
                <PhotoInput
                  compact
                  onUploaded={(file) =>
                    onChange((previous) =>
                      previous.map((v) =>
                        v.damage_id === d.id ? { ...v, photo_file_ids: [...v.photo_file_ids, file.id] } : v,
                      ),
                    )
                  }
                />
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
