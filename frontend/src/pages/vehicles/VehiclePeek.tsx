/** The tabs «Inspections» and «Rentals» of the card of a vehicle (customer's remark of
 * 24.11.2026): a list at the left, the latest first, scrolled alone; what is chosen in it is
 * shown at the right, and the page itself stays in place. The full record opens by its link. */
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { fileUrl } from "../../api/client";
import { useApi } from "../../api/queries";
import type { Client, Inspection, RentalSummary, Vehicle } from "../../api/types";
import { Card, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { clientName } from "../clients/ClientsPage";
import { stateBadge, useInspections, useVehicleDamages } from "../inspections/data";
import { DamageList } from "../inspections/DamagesEditor";
import { newPath } from "../inspections/paths";
import { useLocations } from "../settings/LocationsPage";

function Split({ list, peek }: { list: ReactNode; peek: ReactNode }) {
  return (
    <div className="pick-split">
      <div className="pick-list">{list}</div>
      <div className="pick-peek">{peek}</div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

export function VehicleInspectionsSplit({ vehicle, zone }: { vehicle: Vehicle; zone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const list = useInspections(`vehicle_card_id=${vehicle.id}`);
  const damages = useVehicleDamages(vehicle.id);
  const [chosen, setChosen] = useState<string | null>(null);
  const moment = (item: Inspection) => (item.state === "planned" ? item.planned_at : item.performed_at);
  const items = [...(list.data?.inspections ?? [])].sort((a, b) => (moment(b) ?? "").localeCompare(moment(a) ?? ""));
  const shown = items.find((item) => item.id === chosen) ?? items[0];
  const photos = (shown?.files ?? []).filter((f) => f.kind === "photo");
  const found = (damages.data?.damages ?? []).filter((d) => d.inspection_id === shown?.id);
  return (
    <Split
      list={
        <Card>
          {can("inspection.create") && vehicle.state === "active" ? (
            <div className="row-actions" style={{ justifyContent: "flex-start", marginBottom: 12 }}>
              <Link className="button secondary small-button" to={companyPath(company, newPath({ vehicle: vehicle.id }))}>
                {t("inspections.new")}
              </Link>
            </div>
          ) : null}
          {list.isLoading ? <Loading /> : null}
          {!list.isLoading && items.length === 0 ? <p className="muted">{t("inspections.none")}</p> : null}
          <div className="inspection-list">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`list-link pick-row${item.id === shown?.id ? " active" : ""}`}
                aria-pressed={item.id === shown?.id}
                onClick={() => setChosen(item.id)}
              >
                <div className="list-link-head">
                  <strong>{t(`inspections.kind_${item.kind}`)}</strong>
                  <span className={`badge ${stateBadge(item.state)}`}>{t(`inspections.state_${item.state}`)}</span>
                </div>
                <div className="muted small">
                  {formatMoment(moment(item), zone, i18n.language)}
                  {" · "}
                  {t(`inspections.application_${item.application}`)}
                  {item.odometer ? ` · ${Number(item.odometer)} ${item.odometer_unit}` : ""}
                </div>
              </button>
            ))}
          </div>
        </Card>
      }
      peek={
        shown ? (
          <Card>
            <div className="list-link-head" style={{ marginBottom: 8 }}>
              <strong>{t(`inspections.kind_${shown.kind}`)}</strong>
              <Link to={companyPath(company, `/inspections/${shown.id}`)}>{t("inspections.open")}</Link>
            </div>
            <dl className="facts">
              <Fact label={t("vehicles.peek_when")}>{formatMoment(moment(shown), zone, i18n.language)}</Fact>
              <Fact label={t("vehicles.odometer")}>
                {shown.odometer ? `${Number(shown.odometer)} ${shown.odometer_unit}` : "—"}
              </Fact>
              <Fact label={t("inspections.fuel")}>{shown.fuel ? Number(shown.fuel) : "—"}</Fact>
              {shown.cleanliness ? <Fact label={t("inspections.cleanliness")}>{shown.cleanliness}</Fact> : null}
            </dl>
            {photos.length ? (
              <div className="peek-photos">
                {photos.map((f) => (
                  <a key={f.file_id} href={fileUrl(f.file_id)} target="_blank" rel="noreferrer">
                    <img src={fileUrl(f.file_id)} alt={f.position ? t(`inspections.position_${f.position}`, { defaultValue: "" }) : ""} />
                  </a>
                ))}
              </div>
            ) : null}
            <h3 style={{ marginTop: 12 }}>{t("inspections.damages")}</h3>
            {found.length === 0 ? <p className="muted small">{t("inspections.no_damages")}</p> : null}
            <DamageList damages={found} />
          </Card>
        ) : null
      }
    />
  );
}

export function VehicleRentalsSplit({ rentals, zone }: { rentals: RentalSummary[]; zone: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const locations = useLocations();
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id],
    `/c/${company.public_id}/clients`,
    can("client.list"),
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const shown = rentals.find((r) => r.id === chosen) ?? rentals[0];
  // A booking and a cancelled one have no actual dates: the planned ones stand for them.
  const start = (r: RentalSummary) => r.actual_start ?? r.booking?.planned_start ?? null;
  const end = (r: RentalSummary) => r.actual_end ?? r.planned_end ?? null;
  const place = (id: string | null) => locations.data?.locations.find((l) => l.id === id)?.name ?? "—";
  const client = clients.data?.clients.find((c) => c.id === shown?.client_id);
  return (
    <Split
      list={
        <Card>
          {rentals.length === 0 ? <p className="muted">{t("vehicles.no_rentals")}</p> : null}
          <div className="inspection-list">
            {rentals.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`list-link pick-row${r.id === shown?.id ? " active" : ""}`}
                aria-pressed={r.id === shown?.id}
                onClick={() => setChosen(r.id)}
              >
                <div className="list-link-head">
                  <strong>
                    {formatMoment(start(r), zone, i18n.language)} — {formatMoment(end(r), zone, i18n.language)}
                  </strong>
                  <span className="badge">{t(`rentals.state_${r.state}`)}</span>
                </div>
              </button>
            ))}
          </div>
        </Card>
      }
      peek={
        shown ? (
          <Card>
            <div className="list-link-head" style={{ marginBottom: 8 }}>
              <strong>{t(`rentals.state_${shown.state}`)}</strong>
              <Link to={companyPath(company, `/rentals/${shown.id}`)}>{t("vehicles.peek_open_rental")}</Link>
            </div>
            <dl className="facts">
              <Fact label={t("vehicles.peek_start")}>{formatMoment(start(shown), zone, i18n.language)}</Fact>
              <Fact label={t("vehicles.peek_end")}>{formatMoment(end(shown), zone, i18n.language)}</Fact>
              <Fact label={t("rentals.client")}>{client ? clientName(client) : shown.booking?.contact || "—"}</Fact>
              <Fact label={t("vehicles.peek_issue_place")}>{place(shown.issue_location_id)}</Fact>
              <Fact label={t("vehicles.peek_return_place")}>{place(shown.return_location_id)}</Fact>
              {shown.booking?.comment ? <Fact label={t("vehicles.peek_comment")}>{shown.booking.comment}</Fact> : null}
              {shown.booking?.cancel_reason ? (
                <Fact label={t("vehicles.peek_cancel_reason")}>{shown.booking.cancel_reason}</Fact>
              ) : null}
            </dl>
          </Card>
        ) : null
      }
    />
  );
}
