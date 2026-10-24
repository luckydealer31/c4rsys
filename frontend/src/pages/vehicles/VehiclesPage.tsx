import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { RentalSummary, Tariff, Vehicle, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Icon } from "../../components/icons";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { VehicleMoves } from "./VehicleMoves";

// Only operational contexts are switched for a vehicle — rental now (decision of 24.10.2026):
// service is implied, the warehouse is reached by a transfer of an own vehicle (§ 8.1.5).
const USAGE_CONTEXTS = ["rental"] as const;

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

/** Garage: vehicles by location — name, VHL, occupancy (free / in rental), current odometer
 * (§ 8.7.2; decision of 24.10.2026). */
type Relation = "own" | "managed" | "client";
const RELATIONS: Relation[] = ["own", "managed", "client"];

export function VehiclesPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const vehicles = useVehicles();
  const locations = useLocations();
  const rentals = useRentals(can("rental.view"));
  // Tabs by relation, as the tabs of «Contacts» (decision of 24.10.2026; § 8.1.6).
  const [params, setParams] = useSearchParams();
  const tab: Relation = RELATIONS.includes(params.get("tab") as Relation)
    ? (params.get("tab") as Relation)
    : "own";
  // Own and managed vehicles are grouped by their location, in the order of the locations;
  // those without a location come last. Client vehicles have no location (§ 8.1.2).
  // Vehicles on a warehouse are stock items, shown in «Warehouse» (§ 8.1.5).
  const list = (vehicles.data?.vehicles ?? []).filter(
    (v) => v.relation === tab && v.storage === null,
  );
  const groups = (tab === "client"
    ? [{ id: "client", name: t("vehicles.client_group"), vehicles: list }]
    : [
    ...(locations.data?.locations ?? []).map((l) => ({
      id: l.id,
      name: l.name,
      vehicles: list.filter((v) => v.location_id === l.id),
    })),
    {
      id: "none",
      name: t("vehicles.no_location"),
      vehicles: list.filter(
        (v) => !v.location_id || !locations.data?.locations.some((l) => l.id === v.location_id),
      ),
    },
  ]).filter((group) => group.vehicles.length > 0);
  const busy = new Set((rentals.data?.rentals ?? []).filter((r) => r.state === "active").map((r) => r.vehicle_card_id));
  return (
    <div className="page">
      <h1>{t("nav.garage")}</h1>
      {can("vehicle.create") ? (
        <div className="actions" style={{ marginTop: 0, marginBottom: 16 }}>
          <Link className="button" to={companyPath(company, `/vehicles/new?relation=${tab}`)}>
            <Icon name="plus" size={18} /> {t("vehicles.create_button")}
          </Link>
        </div>
      ) : null}
      <div className="tabs" role="tablist">
        {RELATIONS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setParams(key === "own" ? {} : { tab: key })}
          >
            {t(`vehicles.tab_${key}`)}
          </button>
        ))}
      </div>
      {vehicles.isLoading ? <Loading /> : null}
      {vehicles.data && list.length === 0 ? (
        <Card>
          <p className="muted">{t("vehicles.none")}</p>
        </Card>
      ) : null}
      {groups.map((group) => (
        <Card key={group.id} title={group.name}>
          <table>
            <thead>
              <tr>
                <th>{t("vehicles.name")}</th>
                {tab === "client" ? null : <th>{t("vehicles.occupancy")}</th>}
                <th>{t("vehicles.odometer")}</th>
              </tr>
            </thead>
            <tbody>
              {group.vehicles.map((vehicle) => (
                <tr key={vehicle.id}>
                  <td>
                    <Link to={companyPath(company, `/vehicles/${vehicle.id}`)}>{vehicle.name}</Link>
                    <div className="muted small">{vehicle.vehicle_id}</div>
                  </td>
                  {tab === "client" ? null : (
                    <td>
                      {busy.has(vehicle.id) ? (
                        <span className="badge warn">{t("vehicles.in_rental")}</span>
                      ) : (
                        <span className="badge ok">{t("vehicles.free")}</span>
                      )}
                    </td>
                  )}
                  <td>
                    {vehicle.odometer ? `${Number(vehicle.odometer.value)} ${vehicle.odometer.unit}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
    </div>
  );
}

/** Identification data and characteristics of the vehicle; a coincidence of VIN or plate with
 * another vehicle is shown as a reason to check (§ 8.6.3). */
function VehicleFacts({ vehicle }: { vehicle: Vehicle }) {
  const { t, i18n } = useTranslation();
  const c = vehicle.characteristics;
  const country = c.plate_country
    ? new Intl.DisplayNames([i18n.language], { type: "region" }).of(c.plate_country)
    : "";
  const option = (prefix: string, value: string) => (value ? t(`${prefix}.${value}`) : "");
  const can = useCan();
  const company = useCompany();
  const owner = vehicle.owner;
  // Not given is said plainly (decision of 24.10.2026).
  const ownerText = !owner
    ? vehicle.relation === "own"
      ? company.display_name
      : t("vehicles.owner_not_given")
    : owner.kind === "company"
      ? (owner.company?.display_name ?? "")
      : owner.kind === "client"
        ? (owner.client?.name ?? "")
      : owner.kind === "profile"
        ? owner.lookup
        : owner.kind === "none"
          ? t("vehicles.owner_not_given")
          : [owner.name, owner.contact].filter(Boolean).join(", ");
  const facts: [string, string][] = [
    [t("vehicles.relation"), t(`vehicles.relation_${vehicle.relation}`)],
    [
      t("vehicles.section_owner"),
      // Only the owner of a managed vehicle is asked to confirm (§ 8.3.1, § 8.3.13).
      vehicle.relation === "managed" && owner && owner.kind !== "none" && !owner.confirmed
        ? `${ownerText} (${t("vehicles.owner_unconfirmed")})`
        : ownerText,
    ],
    [t("vehicles.vehicle_type"), c.vehicle_type ? t(`vehicles.type_${c.vehicle_type}`) : ""],
    [t("vehicles.make"), c.make_name],
    [t("vehicles.model"), c.model_name],
    [t("vehicles.year"), c.year ? String(c.year) : ""],
    [t("vehicles.plate"), [c.plate, country].filter(Boolean).join(", ")],
    [t("vehicles.vin"), c.vin],
    [t("vehicles.color"), c.color],
    [t("vehicles.body_type"), option("vehicles.body", c.body_type)],
    [t("vehicles.fuel_type"), option("vehicles.fuel", c.fuel_type)],
    [t("vehicles.transmission"), option("vehicles.gearbox", c.transmission)],
    [t("vehicles.drive"), option("vehicles.drives", c.drive)],
    [t("vehicles.engine_volume_l"), c.engine_volume_l ?? ""],
    [t("vehicles.power_hp"), c.power_hp ? String(c.power_hp) : ""],
    [t("vehicles.seats"), c.seats ? String(c.seats) : ""],
    [t("vehicles.doors"), c.doors !== null ? String(c.doors) : ""],
  ];
  return (
    <Card title={t("vehicles.section_characteristics")}>
      {c.identifier_matches.length ? (
        <div className="alert warn">
          {c.identifier_matches.map((m) => t(`vehicles.match_${m}`)).join(" ")}
        </div>
      ) : null}
      <dl className="facts">
        {facts
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      {can("vehicle.edit") ? (
        <div className="actions">
          <Link className="button secondary" to={companyPath(company, `/vehicles/${vehicle.id}/edit`)}>
            {t("common.edit")}
          </Link>
        </div>
      ) : null}
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
  // Rental, moves, tariffs and contexts concern only own and managed vehicles in the garage
  // (§ 8.1.2, § 8.1.5).
  const stored = data.storage !== null;
  const isClient = data.relation === "client" || stored;

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
      {stored ? (
        <div className="alert warn">
          {t("vehicles.in_warehouse", {
            location: locations.data?.locations.find((l) => l.id === data.storage?.location_id)?.name ?? "—",
          })}
        </div>
      ) : null}
      <VehicleFacts vehicle={data} />
      <Card>
        <p>
          {data.vehicle_id} · {t(`vehicles.relation_${data.relation}`)}
          {isClient ? "" : ` · ${location?.name ?? "—"}`} · {t(`vehicles.unit_${data.odometer_unit}`)}
        </p>
        <p>
          {t("vehicles.odometer")}:{" "}
          {data.odometer
            ? `${Number(data.odometer.value)} ${data.odometer.unit} (${formatMoment(data.odometer.measured_at, timeZone, i18n.language)})`
            : "—"}
        </p>
        {isClient ? null : (
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
        )}
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
        {/* A client vehicle is only serviced: nothing to allow (decision of 24.10.2026). */}
        {isClient ? null : <h3 style={{ marginTop: 16 }}>{t("vehicles.contexts")}</h3>}
        {(isClient ? [] : USAGE_CONTEXTS).map((type) => (
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
        {isClient ? null : <p className="muted small">{t("vehicles.contexts_hint")}</p>}
        <ErrorMessage error={update.error} />
      </Card>
      {data.relation === "own" && !stored && data.contexts.length === 0 ? (
        <TransferToWarehouse vehicle={data} />
      ) : null}
      {isClient ? null : <VehicleTariffs vehicleId={data.id} />}
      {isClient ? null : <VehicleMoves vehicle={data} />}
      {!isClient && can("rental.view") ? (
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

/** Offered when an own vehicle has no operational context: transfer to a warehouse as the same
 * asset with all its history (decision of 24.10.2026; § 8.1.5, § 8.1.7). */
function TransferToWarehouse({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const locations = useLocations();
  const [target, setTarget] = useState("");
  const transfer = useCommand<{ context_id: string; expected_version: number }, unknown>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/to-warehouse`,
  );
  if (!can("vehicle.transfer_to_warehouse")) return null;
  const warehouses = (locations.data?.locations ?? []).flatMap((l) =>
    l.state === "deactivated"
      ? []
      : l.contexts
          .filter((c) => c.type === "warehouse" && c.state === "enabled")
          .map((c) => ({ id: c.id, name: l.name })),
  );
  return (
    <Card title={t("vehicles.transfer_title")}>
      <p>{t("vehicles.transfer_text")}</p>
      {warehouses.length === 0 ? (
        <p className="muted">{t("vehicles.transfer_no_warehouse")}</p>
      ) : (
        <div className="row">
          <Field label={t("vehicles.transfer_target")}>
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">—</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      <ErrorMessage error={transfer.error} />
      {warehouses.length ? (
        <div className="actions">
          <button
            type="button"
            disabled={!target || transfer.pending}
            onClick={async () => {
              if ((await transfer.run({ context_id: target, expected_version: vehicle.version })) !== null) {
                await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
                await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
              }
            }}
          >
            {t("vehicles.transfer_button")}
          </button>
        </div>
      ) : null}
    </Card>
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
        <Link to={companyPath(company, "/rentals/tariffs")}>{t("settings.tariffs")}</Link>
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
      <Link to={companyPath(company, `/rentals/tariffs/${tariff.id}`)}>{tariff.name}</Link>
      {tariff.state === "archived" ? <span className="badge"> {t("tariffs.archived")}</span> : null}
    </p>
  );
}
