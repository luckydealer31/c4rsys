/** A damage of the vehicle (§ 10.5): its fixed facts, the mark on the scheme, photos, the
 * history of its states and the estimate (§ 10.5.3–10.5.5). It is created only by an inspection
 * — nothing here creates one. */
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import type { ApiError } from "../../api/client";
import type { Damage } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useVehicles } from "../vehicles/VehiclesPage";
import { stateBadge, useDamage, useRefreshInspections } from "./data";
import { damageText } from "./DamagesEditor";
import { useInspectionZone } from "./InspectionPage";
import { PhotoThumb } from "./Media";
import { VehicleScheme, type SchemeId } from "./schemes";

/** An amount without the trailing zeros of its storage. */
export function amount(value: string): string {
  return String(Number(value));
}

export function DamagePage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const query = useDamage(id);
  const vehicles = useVehicles();
  const damage = query.data?.damage;
  const vehicle = vehicles.data?.vehicles.find((v) => v.id === damage?.vehicle_card_id);
  const zone = useInspectionZone(null, vehicle);
  if (query.isLoading) return <div className="page"><Loading /></div>;
  if (!damage) return <div className="page"><ErrorMessage error={query.error as ApiError} /></div>;
  const when = (value: string | null) => formatMoment(value, zone, i18n.language);
  const scheme = damage.zone_scheme ? (damage.zone_scheme as SchemeId) : null;
  const photos = (damage.history ?? []).flatMap((e) => e.files);
  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, `/vehicles/${damage.vehicle_card_id}`)}>← {vehicle?.name ?? t("inspections.vehicle")}</Link>
      </p>
      <h1>
        {t("inspections.damage")}{" "}
        <span className={`badge ${stateBadge(damage.state)}`}>{t(`inspections.damage_state_${damage.state}`)}</span>
      </h1>
      <Card>
        <dl className="facts">
          <dt>{t("inspections.damage_description")}</dt>
          <dd>{damageText(damage) || "—"}</dd>
          <dt>{t("inspections.performed_at")}</dt>
          <dd>{when(damage.performed_at)}</dd>
          <dt>{t("inspections.odometer", { unit: damage.odometer_unit })}</dt>
          <dd>{damage.odometer ? Number(damage.odometer) : "—"}</dd>
          <dt>{t("inspections.source_inspection")}</dt>
          <dd>
            <Link to={companyPath(company, `/inspections/${damage.inspection_id}`)}>{t("inspections.open")}</Link>
          </dd>
          {damage.rental_id ? (
            <>
              <dt>{t("inspections.rental")}</dt>
              <dd>
                <Link to={companyPath(company, `/rentals/${damage.rental_id}`)}>{t("inspections.open_rental")}</Link>
                {damage.attributed_to_rental ? ` · ${t("inspections.attributed_short")}` : ""}
              </dd>
            </>
          ) : null}
          <dt>{t("inspections.estimate")}</dt>
          <dd>{damage.estimate ? `${amount(damage.estimate.amount)} ${damage.estimate.currency}` : t("inspections.not_estimated")}</dd>
          {damage.charged !== null ? (
            <>
              <dt>{t("inspections.charged")}</dt>
              <dd>{amount(damage.charged)}</dd>
            </>
          ) : null}
        </dl>
        {scheme && damage.zone_x !== null ? (
          <div className="scheme-box" style={{ marginTop: 12 }}>
            <VehicleScheme
              id={scheme}
              marks={[{ x: Number(damage.zone_x), y: Number(damage.zone_y), view: null, label: "1" }]}
            />
          </div>
        ) : null}
        {photos.length ? (
          <div className="media-row" style={{ marginTop: 12 }}>
            {photos.map((f) => (
              <PhotoThumb key={f.file_id} fileId={f.file_id} />
            ))}
          </div>
        ) : null}
      </Card>
      <EstimateCard damage={damage} />
      <Card title={t("inspections.damage_history")}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("inspections.when")}</th>
                <th>{t("inspections.event")}</th>
                <th>{t("inspections.basis")}</th>
                <th>{t("inspections.estimate")}</th>
              </tr>
            </thead>
            <tbody>
              {(damage.history ?? []).map((e) => (
                <tr key={e.id}>
                  <td>{when(e.occurred_at)}</td>
                  <td>
                    {t(`inspections.event_${e.kind}`)}
                    {e.state_before && e.state_before !== e.state_after ? (
                      <div className="muted small">
                        {t(`inspections.damage_state_${e.state_before}`)} → {t(`inspections.damage_state_${e.state_after}`)}
                      </div>
                    ) : null}
                    {e.comment ? <div className="muted small">{e.comment}</div> : null}
                    {e.files.length ? (
                      <div className="media-row">
                        {e.files.map((f) => (
                          <PhotoThumb key={f.file_id} fileId={f.file_id} size={56} />
                        ))}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {e.basis_type === "inspection" && e.basis_id ? (
                      <Link to={companyPath(company, `/inspections/${e.basis_id}`)}>{t("inspections.basis_inspection")}</Link>
                    ) : e.basis_type === "service_job" ? (
                      t("inspections.basis_service_job")
                    ) : (
                      "—"
                    )}
                    {e.odometer ? <div className="muted small">{Number(e.odometer)}</div> : null}
                  </td>
                  <td>{e.estimate ? `${amount(e.estimate.amount)} ${e.estimate.currency}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

/** Empty is «not estimated», 0 is an estimate (§ 10.5.5); in a rental the estimate becomes the
 * client's charge, a waiver is a discount (§ 10.5.3). */
export function EstimateCard({ damage }: { damage: Damage }) {
  const { t } = useTranslation();
  const can = useCan();
  const permission = damage.attributed_to_rental ? "rental.manual_charge" : "inspection.conduct";
  if (damage.state === "erroneous") return null;
  return (
    <Card title={t("inspections.estimate")}>
      <p className="muted small">
        {damage.attributed_to_rental ? t("inspections.estimate_rental_hint") : t("inspections.estimate_outside_hint")}
      </p>
      {can(permission) ? <EstimateForm damage={damage} /> : null}
    </Card>
  );
}

export function EstimateForm({ damage, compact }: { damage: Damage; compact?: boolean }) {
  const { t } = useTranslation();
  const company = useCompany();
  const refresh = useRefreshInspections();
  const [amount, setAmount] = useState(damage.estimate ? String(Number(damage.estimate.amount)) : "");
  const [currency, setCurrency] = useState(damage.estimate?.currency ?? company.main_currency ?? "");
  const command = useCommand<Record<string, unknown>, { damage: Damage }>(
    `/c/${company.public_id}/damages/${damage.id}/estimate`,
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await command.run({
      expected_version: damage.version,
      estimate: { amount: amount.trim().replace(",", "."), currency: currency.trim().toUpperCase() },
    });
    if (result) await refresh();
  }
  return (
    <form onSubmit={submit} className={compact ? "row compact-form" : "row"}>
      <Field label={t("inspections.estimate_amount")}>
        <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>
      <Field label={t("inspections.currency")}>
        <input value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
      </Field>
      <div className="actions">
        <button type="submit" disabled={command.pending || !amount.trim() || currency.trim().length !== 3}>
          {t("inspections.save_estimate")}
        </button>
      </div>
      <ErrorMessage error={command.error} />
    </form>
  );
}
