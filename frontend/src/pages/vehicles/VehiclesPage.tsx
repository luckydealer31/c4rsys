import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { useApi } from "../../api/queries";
import type { RentalSummary, Tariff, Vehicle, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { VehicleMoves } from "./VehicleMoves";

const USAGE_CONTEXTS = ["rental", "service", "warehouse"] as const;

export function useVehicles() {
  const company = useCompany();
  return useApi<{ vehicles: Vehicle[] }>(["vehicles", company.public_id], `/c/${company.public_id}/vehicles`);
}

export function useRentals(enabled = true) {
  const company = useCompany();
  return useApi<{ rentals: RentalSummary[] }>(
    ["rentals", company.public_id],
    `/c/${company.public_id}/rentals`,
    enabled,
  );
}

/** Garage: name, VHL, location, occupancy (free / in rental), current odometer (§ 8.7.2). */
export function VehiclesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const vehicles = useVehicles();
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const names = new Map((locations.data?.locations ?? []).map((l) => [l.id, l.name]));
  const busy = new Set((rentals.data?.rentals ?? []).filter((r) => r.state === "active").map((r) => r.vehicle_card_id));
  return (
    <div className="page">
      <h1>{t("nav.garage")}</h1>
      <Card>
        {vehicles.isLoading ? <Loading /> : null}
        {vehicles.data?.vehicles.length === 0 ? <p className="muted">{t("vehicles.none")}</p> : null}
        <table>
          <thead>
            <tr>
              <th>{t("vehicles.name")}</th>
              <th>{t("vehicles.location")}</th>
              <th>{t("vehicles.occupancy")}</th>
              <th>{t("vehicles.odometer")}</th>
            </tr>
          </thead>
          <tbody>
            {(vehicles.data?.vehicles ?? []).map((vehicle) => (
              <tr key={vehicle.id}>
                <td>
                  <Link to={companyPath(company, `/vehicles/${vehicle.id}`)}>{vehicle.name}</Link>
                  <div className="muted small">{vehicle.vehicle_id}</div>
                </td>
                <td>{vehicle.location_id ? names.get(vehicle.location_id) : "—"}</td>
                <td>
                  {busy.has(vehicle.id) ? (
                    <span className="badge warn">{t("vehicles.in_rental")}</span>
                  ) : (
                    <span className="badge ok">{t("vehicles.free")}</span>
                  )}
                </td>
                <td>{vehicle.odometer ? `${Number(vehicle.odometer.value)} ${vehicle.odometer.unit}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {can("vehicle.create") ? <CreateVehicle /> : null}
    </div>
  );
}

function CreateVehicle() {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const locations = useLocations();
  const [form, setForm] = useState({ name: "", location_id: "", odometer_unit: "km" });
  const create = useCommand<Record<string, string>, { vehicle: Vehicle }>(`/c/${company.public_id}/vehicles`);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((await create.run(form)) !== null) {
      setForm({ ...form, name: "" });
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <Card title={t("vehicles.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("vehicles.name")} hint={t("vehicles.name_hint")}>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("vehicles.location")}>
            <select value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value })}>
              <option value="">—</option>
              {(locations.data?.locations ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("vehicles.odometer_unit")}>
            <select value={form.odometer_unit} onChange={(e) => setForm({ ...form, odometer_unit: e.target.value })}>
              <option value="km">{t("vehicles.unit_km")}</option>
              <option value="mi">{t("vehicles.unit_mi")}</option>
            </select>
          </Field>
        </div>
        <p className="muted small">{t("vehicles.own_only")}</p>
        <ErrorMessage error={create.error} />
        <div className="actions">
          <button type="submit" disabled={!form.name.trim() || !form.location_id || create.pending}>
            {t("vehicles.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** Vehicle card: name, relation, accounting location, odometer unit, allowed contexts, tariffs,
 * rentals (§ 8.1, § 8.5, § 8.7.7). */
export function VehiclePage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const vehicle = useApi<{ vehicle: Vehicle }>(["vehicle", company.public_id, id], `/c/${company.public_id}/vehicles/${id}`);
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  const update = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/vehicles/${id}`, "PATCH");
  const [name, setName] = useState<string | null>(null);
  if (vehicle.isLoading) return <div className="page"><Loading /></div>;
  const data = vehicle.data?.vehicle;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;
  const location = locations.data?.locations.find((l) => l.id === data.location_id);
  const timeZone = location?.timezone ?? company.timezone ?? "UTC";
  const own = (rentals.data?.rentals ?? []).filter((r) => r.vehicle_card_id === data.id);

  const current = data;
  async function save(changes: Record<string, unknown>) {
    if ((await update.run({ ...changes, expected_version: current.version })) !== null) {
      setName(null);
      await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, id] });
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  function toggleContext(type: string) {
    const next = current.contexts.includes(type)
      ? current.contexts.filter((c) => c !== type)
      : [...current.contexts, type];
    void save({ contexts: next });
  }

  return (
    <div className="page">
      <h1>{data.name}</h1>
      <Card>
        <p>
          {data.vehicle_id} · {t(`vehicles.relation_${data.relation}`)} · {location?.name ?? "—"} ·{" "}
          {t(`vehicles.unit_${data.odometer_unit}`)}
        </p>
        <p>
          {t("vehicles.odometer")}:{" "}
          {data.odometer
            ? `${Number(data.odometer.value)} ${data.odometer.unit} (${formatMoment(data.odometer.measured_at, timeZone, i18n.language)})`
            : "—"}
        </p>
        <p>
          {t("vehicles.rental_readiness")}:{" "}
          {data.rental_readiness.ready ? (
            <span className="badge ok">{t("setup.ready")}</span>
          ) : (
            <span className="badge warn">
              {data.rental_readiness.missing.map((m) => t(`vehicles.missing_${m}`)).join("; ")}
            </span>
          )}
        </p>
        {can("vehicle.edit") ? (
          <>
            <div className="row">
              <Field label={t("vehicles.name")}>
                <input value={name ?? data.name} onChange={(e) => setName(e.target.value)} />
              </Field>
            </div>
            <div className="actions">
              <button type="button" disabled={!name || name === data.name} onClick={() => void save({ name })}>
                {t("common.save")}
              </button>
            </div>
          </>
        ) : null}
        <h3 style={{ marginTop: 16 }}>{t("vehicles.contexts")}</h3>
        {USAGE_CONTEXTS.map((type) => (
          <label key={type} style={{ marginRight: 16 }}>
            <input
              type="checkbox"
              disabled={!can("vehicle.edit")}
              checked={data.contexts.includes(type)}
              onChange={() => toggleContext(type)}
            />{" "}
            {t(`contexts.${type}`)}
          </label>
        ))}
        <ErrorMessage error={update.error} />
      </Card>
      <VehicleTariffs vehicleId={data.id} />
      <VehicleMoves vehicle={data} />
      {can("rental.view") ? (
        <Card title={t("vehicles.rentals")}>
          {own.length === 0 ? <p className="muted">{t("vehicles.no_rentals")}</p> : null}
          <table>
            <tbody>
              {own.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={companyPath(company, `/rentals/${r.id}`)}>
                      {formatMoment(r.actual_start, timeZone, i18n.language)} —{" "}
                      {formatMoment(r.actual_end, timeZone, i18n.language)}
                    </Link>
                  </td>
                  <td>{t(`rentals.state_${r.state}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}

/** Tariffs assigned to this vehicle with their terms. */
function VehicleTariffs({ vehicleId }: { vehicleId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const tariffs = useApi<{ tariffs: Tariff[] }>(
    ["tariffs", company.public_id],
    `/c/${company.public_id}/tariffs`,
    can("tariff.view"),
  );
  if (!can("tariff.view")) return null;
  return (
    <Card title={t("vehicles.tariffs")}>
      {(tariffs.data?.tariffs ?? []).map((tariff) => (
        <AssignedTariff key={tariff.id} tariff={tariff} vehicleId={vehicleId} />
      ))}
      <p className="muted small">
        {t("vehicles.tariffs_hint")}{" "}
        <Link to={companyPath(company, "/settings/tariffs")}>{t("settings.tariffs")}</Link>
      </p>
    </Card>
  );
}

function AssignedTariff({ tariff, vehicleId }: { tariff: Tariff; vehicleId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const terms = useApi<{ vehicles: VehicleTerms[] }>(
    ["tariff-vehicles", company.public_id, tariff.id],
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles`,
  );
  const mine = terms.data?.vehicles.find((v) => v.vehicle_card_id === vehicleId);
  if (!mine) return null;
  return (
    <p>
      <Link to={companyPath(company, `/settings/tariffs/${tariff.id}`)}>{tariff.name}</Link>
      {tariff.state === "archived" ? <span className="badge"> {t("tariffs.archived")}</span> : null}
    </p>
  );
}
