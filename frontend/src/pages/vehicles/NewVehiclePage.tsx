import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, Client, Vehicle, VehicleOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { clientName } from "../clients/ClientsPage";
import { countries } from "../settings/CompanyProfilePage";
import { useLocations } from "../settings/LocationsPage";

const OTHER = "__other";

type Form = {
  transport_class: string;
  vehicle_type: string;
  make: string;
  make_other: string;
  model: string;
  model_other: string;
  year: string;
  plate: string;
  plate_country: string;
  vin: string;
  color: string;
  body_type: string;
  fuel_type: string;
  transmission: string;
  drive: string;
  engine_volume_l: string;
  power_hp: string;
  seats: string;
  doors: string;
  location_id: string;
  odometer_unit: string;
  relation: string;
  owner_kind: string;
  owner_lookup: string;
  owner_name: string;
  owner_contact: string;
  owner_client_id: string;
  client_owner_kind: string;
};

const OWNER_KINDS = ["profile", "person", "organization", "none"] as const;
// A client vehicle: a client from the base, or an owner by hand without a profile (24.10.2026).
const CLIENT_OWNER_KINDS = ["client", "person", "organization", "none"] as const;

const EMPTY: Form = {
  transport_class: "ground",
  vehicle_type: "passenger_car",
  make: "",
  make_other: "",
  model: "",
  model_other: "",
  year: "",
  plate: "",
  plate_country: "",
  vin: "",
  color: "",
  body_type: "",
  fuel_type: "",
  transmission: "",
  drive: "",
  engine_volume_l: "",
  power_hp: "",
  seats: "",
  doors: "",
  location_id: "",
  odometer_unit: "km",
  relation: "own",
  owner_kind: "profile",
  owner_lookup: "",
  owner_name: "",
  owner_contact: "",
  owner_client_id: "",
  client_owner_kind: "client",
};

/** Full creation card of a vehicle (customer's decisions of 24.10.2026; § 8.1.8–8.1.12): class
 * and type first, then make and model from the catalog (or «Other» as text), identification,
 * characteristics and accounting. The name is formed from the make, the model and the plate
 * until the user writes their own. */
export function NewVehiclePage() {
  return <VehicleForm />;
}

/** «Edit» of a vehicle (decision of 24.10.2026): the same card with the current data; relation,
 * location and odometer unit are not changed here. */
export function EditVehiclePage() {
  const company = useCompany();
  const { id = "" } = useParams();
  const vehicle = useApi<{ vehicle: Vehicle }>(
    ["vehicle", company.public_id, id],
    `/c/${company.public_id}/vehicles/${id}`,
  );
  if (!vehicle.data) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  return <VehicleForm vehicle={vehicle.data.vehicle} />;
}

function formOf(vehicle: Vehicle): Form {
  const c = vehicle.characteristics;
  const o = vehicle.owner;
  const text = (value: number | string | null) => (value === null ? "" : String(value));
  return {
    ...EMPTY,
    transport_class: c.transport_class || "ground",
    vehicle_type: c.vehicle_type || "passenger_car",
    make: c.make ?? (c.make_name ? OTHER : ""),
    make_other: c.make ? "" : c.make_name,
    model: c.model ?? (c.model_name && c.make ? OTHER : ""),
    model_other: c.model ? "" : c.model_name,
    year: text(c.year),
    plate: c.plate,
    plate_country: c.plate_country,
    vin: c.vin,
    color: c.color,
    body_type: c.body_type,
    fuel_type: c.fuel_type,
    transmission: c.transmission,
    drive: c.drive,
    engine_volume_l: c.engine_volume_l ?? "",
    power_hp: text(c.power_hp),
    seats: text(c.seats),
    doors: text(c.doors),
    location_id: vehicle.location_id ?? "",
    odometer_unit: vehicle.odometer_unit,
    relation: vehicle.relation,
    owner_kind: o && vehicle.relation === "managed" ? o.kind : "profile",
    owner_lookup: o?.lookup ?? "",
    owner_name: o?.name ?? "",
    owner_contact: o?.contact ?? "",
    owner_client_id: o?.client?.id ?? "",
    client_owner_kind: o && vehicle.relation === "client" && o.kind !== "none" ? o.kind : "client",
  };
}

function VehicleForm({ vehicle }: { vehicle?: Vehicle }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const navigate = useNavigate();
  const locations = useLocations();
  const can = useCan();
  const options = useApi<VehicleOptions>(["vehicle-options"], "/catalog/vehicle-options");
  const clients = useApi<{ clients: Client[] }>(
    ["clients", company.public_id, ""],
    `/c/${company.public_id}/clients?q=`,
    can("client.list"),
  );
  const [params] = useSearchParams();
  const initialRelation = params.get("relation");
  const [form, setForm] = useState<Form>(
    vehicle
      ? formOf(vehicle)
      : {
          ...EMPTY,
          relation:
            initialRelation === "managed" || initialRelation === "client" ? initialRelation : "own",
        },
  );
  const [customName, setCustomName] = useState<string | null>(vehicle ? vehicle.name : null);
  const create = useCommand<Record<string, unknown>, { vehicle: Vehicle; warnings: string[] }>(
    vehicle
      ? `/c/${company.public_id}/vehicles/${vehicle.id}/details`
      : `/c/${company.public_id}/vehicles`,
    vehicle ? "PUT" : "POST",
  );
  const set = (changes: Partial<Form>) => setForm({ ...form, ...changes });

  const hasCatalog = options.data?.catalog_types.includes(form.vehicle_type) ?? false;
  const makes = useApi<{ makes: CatalogMake[] }>(
    ["vehicle-makes", form.vehicle_type],
    `/catalog/vehicle-makes?type=${form.vehicle_type}`,
    hasCatalog,
  );
  const usesCatalogMake = hasCatalog && form.make !== "" && form.make !== OTHER;
  const models = useApi<{ models: CatalogModel[] }>(
    ["vehicle-models", form.make],
    `/catalog/vehicle-makes/${encodeURIComponent(form.make)}/models`,
    usesCatalogMake,
  );
  const countryList = useMemo(() => countries(i18n.language), [i18n.language]);

  const makeName = usesCatalogMake
    ? (makes.data?.makes.find((m) => m.code === form.make)?.name ?? "")
    : form.make_other.trim();
  const modelName =
    usesCatalogMake && form.model !== OTHER
      ? (models.data?.models.find((m) => m.code === form.model)?.name ?? "")
      : form.model_other.trim();
  const autoName = [makeName, modelName, form.plate.trim()].filter(Boolean).join(" ");
  const name = customName ?? autoName;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const number = (value: string) => (value.trim() === "" ? undefined : Number(value));
    const body: Record<string, unknown> = {
      name,
      location_id: form.location_id,
      odometer_unit: form.odometer_unit,
      transport_class: form.transport_class,
      vehicle_type: form.vehicle_type,
      year: number(form.year),
      plate: form.plate,
      plate_country: form.plate_country,
      vin: form.vin,
      color: form.color,
      body_type: form.body_type,
      fuel_type: form.fuel_type,
      transmission: form.transmission,
      drive: form.drive,
      engine_volume_l: form.engine_volume_l || undefined,
      power_hp: number(form.power_hp),
      seats: number(form.seats),
      doors: number(form.doors),
      relation: form.relation,
    };
    if (form.relation === "client") {
      const kind = form.client_owner_kind === "client" && !form.owner_client_id ? "none" : form.client_owner_kind;
      body.owner_kind = kind;
      if (kind === "client") body.owner_client_id = form.owner_client_id;
      if (kind === "person" || kind === "organization") {
        body.owner_name = form.owner_name;
        body.owner_contact = form.owner_contact;
      }
    }
    if (form.relation === "managed") {
      body.owner_kind = form.owner_kind;
      if (form.owner_kind === "profile") body.owner_lookup = form.owner_lookup;
      if (form.owner_kind === "person" || form.owner_kind === "organization") {
        body.owner_name = form.owner_name;
        body.owner_contact = form.owner_contact;
      }
    }
    if (usesCatalogMake) {
      body.make = form.make;
      if (form.model && form.model !== OTHER) body.model = form.model;
      else body.model_other = form.model_other;
    } else {
      body.make_other = form.make_other;
      body.model_other = form.model_other;
    }
    if (vehicle) {
      // Relation, location and unit stay as they are; the version guards the change.
      delete body.relation;
      delete body.location_id;
      delete body.odometer_unit;
      body.expected_version = vehicle.version;
    }
    const result = await create.run(body);
    if (result !== null) {
      if (vehicle) {
        await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, vehicle.id] });
      }
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
      navigate(companyPath(company, `/vehicles/${result.vehicle.id}`));
    }
  }

  if (!options.data) {
    return (
      <div className="page">
        <Loading />
      </div>
    );
  }
  const o = options.data;
  const popular = (makes.data?.makes ?? []).filter((m) => m.popular);
  const choose = (key: keyof Form, list: string[], prefix: string) => (
    <select value={form[key]} onChange={(e) => set({ [key]: e.target.value })}>
      <option value="">—</option>
      {list.map((code) => (
        <option key={code} value={code}>
          {t(`${prefix}.${code}`)}
        </option>
      ))}
    </select>
  );
  const makeLabel = (m: CatalogMake) =>
    i18n.language === "ru" && m.name_cyrillic && m.name_cyrillic !== m.name
      ? `${m.name} (${m.name_cyrillic})`
      : m.name;

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, vehicle ? `/vehicles/${vehicle.id}` : "/vehicles")}>
          ← {vehicle ? vehicle.name : t("nav.garage")}
        </Link>
      </p>
      <h1>{vehicle ? t("vehicles.edit_title") : t("vehicles.create_title")}</h1>
      <form className="stack" onSubmit={submit}>
        <Card title={t("vehicles.section_kind")}>
          {/* Only ground transport exists for now, so its class is not asked (decision of
              24.10.2026); the choice returns with air and water. */}
          <Field label={t("vehicles.vehicle_type")}>
            <select
              value={form.vehicle_type}
              onChange={(e) =>
                set({ vehicle_type: e.target.value, make: "", model: "", make_other: "", model_other: "" })
              }
            >
              {o.vehicle_types.map((code) => (
                <option key={code} value={code}>
                  {t(`vehicles.type_${code}`)}
                </option>
              ))}
            </select>
          </Field>
        </Card>

        <Card title={t("vehicles.section_make")}>
          <div className="row">
            {hasCatalog ? (
              <Field label={t("vehicles.make")}>
                <select
                  value={form.make}
                  onChange={(e) => set({ make: e.target.value, model: "", model_other: "" })}
                >
                  <option value="">—</option>
                  {popular.length ? (
                    <optgroup label={t("vehicles.popular_makes")}>
                      {popular.map((m) => (
                        <option key={`p-${m.code}`} value={m.code}>
                          {makeLabel(m)}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  <optgroup label={t("vehicles.all_makes")}>
                    {(makes.data?.makes ?? []).map((m) => (
                      <option key={m.code} value={m.code}>
                        {makeLabel(m)}
                      </option>
                    ))}
                  </optgroup>
                  <option value={OTHER}>{t("vehicles.other_value")}</option>
                </select>
              </Field>
            ) : null}
            {!hasCatalog || form.make === OTHER ? (
              <Field label={hasCatalog ? t("vehicles.make_other") : t("vehicles.make")}>
                <input value={form.make_other} onChange={(e) => set({ make_other: e.target.value })} />
              </Field>
            ) : null}
            {usesCatalogMake ? (
              <Field label={t("vehicles.model")}>
                <select value={form.model} onChange={(e) => set({ model: e.target.value })}>
                  <option value="">—</option>
                  {(models.data?.models ?? []).map((m) => (
                    <option key={m.code} value={m.code}>
                      {m.name}
                      {m.year_from ? ` (${m.year_from}–${m.year_to ?? ""})` : ""}
                    </option>
                  ))}
                  <option value={OTHER}>{t("vehicles.other_value")}</option>
                </select>
              </Field>
            ) : null}
            {!usesCatalogMake || form.model === OTHER ? (
              <Field label={usesCatalogMake ? t("vehicles.model_other") : t("vehicles.model")}>
                <input value={form.model_other} onChange={(e) => set({ model_other: e.target.value })} />
              </Field>
            ) : null}
            <Field label={t("vehicles.year")}>
              <input
                type="number"
                inputMode="numeric"
                value={form.year}
                onChange={(e) => set({ year: e.target.value })}
              />
            </Field>
          </div>
          {!hasCatalog ? <p className="muted small">{t("vehicles.catalog_later")}</p> : null}
        </Card>

        <Card title={t("vehicles.section_identification")}>
          <div className="row">
            <Field label={t("vehicles.vin")} hint={t("vehicles.vin_hint")}>
              <input value={form.vin} onChange={(e) => set({ vin: e.target.value })} />
            </Field>
            <Field label={t("vehicles.plate")}>
              <input value={form.plate} onChange={(e) => set({ plate: e.target.value })} />
            </Field>
            <Field label={t("vehicles.plate_country")}>
              <select value={form.plate_country} onChange={(e) => set({ plate_country: e.target.value })}>
                <option value="">—</option>
                {countryList.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </Card>

        <Card title={t("vehicles.section_characteristics")}>
          <div className="row">
            <Field label={t("vehicles.color")}>
              <input value={form.color} onChange={(e) => set({ color: e.target.value })} />
            </Field>
            <Field label={t("vehicles.body_type")}>{choose("body_type", o.body_types, "vehicles.body")}</Field>
            <Field label={t("vehicles.fuel_type")}>{choose("fuel_type", o.fuel_types, "vehicles.fuel")}</Field>
            <Field label={t("vehicles.transmission")}>
              {choose("transmission", o.transmissions, "vehicles.gearbox")}
            </Field>
            <Field label={t("vehicles.drive")}>{choose("drive", o.drives, "vehicles.drives")}</Field>
          </div>
          <div className="row">
            <Field label={t("vehicles.engine_volume_l")}>
              <input
                inputMode="decimal"
                value={form.engine_volume_l}
                onChange={(e) => set({ engine_volume_l: e.target.value.replace(",", ".") })}
              />
            </Field>
            <Field label={t("vehicles.power_hp")}>
              <input type="number" value={form.power_hp} onChange={(e) => set({ power_hp: e.target.value })} />
            </Field>
            <Field label={t("vehicles.seats")}>
              <input type="number" value={form.seats} onChange={(e) => set({ seats: e.target.value })} />
            </Field>
            <Field label={t("vehicles.doors")}>
              <input type="number" value={form.doors} onChange={(e) => set({ doors: e.target.value })} />
            </Field>
          </div>
        </Card>

        <Card title={t("vehicles.section_owner")}>
          <div className="choice-row">
            <label>
              <input
                type="radio"
                name="relation"
                disabled={Boolean(vehicle)}
                checked={form.relation === "own"}
                onChange={() => set({ relation: "own" })}
              />{" "}
              {t("vehicles.owner_company", { company: company.display_name })}
            </label>
            <label>
              <input
                type="radio"
                name="relation"
                disabled={Boolean(vehicle)}
                checked={form.relation === "managed"}
                onChange={() => set({ relation: "managed" })}
              />{" "}
              {t("vehicles.owner_other")}
            </label>
            <label>
              <input
                type="radio"
                name="relation"
                disabled={Boolean(vehicle)}
                checked={form.relation === "client"}
                onChange={() => set({ relation: "client" })}
              />{" "}
              {t("vehicles.owner_client")}
            </label>
          </div>
          {form.relation === "own" ? (
            <dl className="facts" style={{ marginTop: 12 }}>
              <dt>{t("companies.display_name")}</dt>
              <dd>{company.display_name}</dd>
              <dt>{t("company.legal_name")}</dt>
              <dd>{company.legal_name || "—"}</dd>
              <dt>{t("companies.company_id")}</dt>
              <dd>{company.public_id}</dd>
            </dl>
          ) : form.relation === "client" ? (
            <div className="stack" style={{ marginTop: 12 }}>
              <Field label={t("vehicles.owner_kind")}>
                <select
                  value={form.client_owner_kind}
                  onChange={(e) => set({ client_owner_kind: e.target.value })}
                >
                  {CLIENT_OWNER_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`vehicles.owner_kind_${kind}`)}
                    </option>
                  ))}
                </select>
              </Field>
              {form.client_owner_kind === "client" ? (
                <Field label={t("vehicles.owner_client_choose")}>
                  <select
                    value={form.owner_client_id}
                    onChange={(e) => set({ owner_client_id: e.target.value })}
                  >
                    <option value="">—</option>
                    {(clients.data?.clients ?? []).map((c) => (
                      <option key={c.id} value={c.id}>
                        {clientName(c)}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : null}
              {form.client_owner_kind === "person" || form.client_owner_kind === "organization" ? (
                <div className="row">
                  <Field label={t(`vehicles.owner_name_${form.client_owner_kind}`)}>
                    <input value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} />
                  </Field>
                  <Field label={t("vehicles.owner_contact")}>
                    <input
                      value={form.owner_contact}
                      onChange={(e) => set({ owner_contact: e.target.value })}
                    />
                  </Field>
                </div>
              ) : null}
              <p className="muted small">{t("vehicles.owner_client_hint")}</p>
            </div>
          ) : (
            <div className="stack" style={{ marginTop: 12 }}>
              <Field label={t("vehicles.owner_kind")}>
                <select value={form.owner_kind} onChange={(e) => set({ owner_kind: e.target.value })}>
                  {OWNER_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`vehicles.owner_kind_${kind}`)}
                    </option>
                  ))}
                </select>
              </Field>
              {form.owner_kind === "profile" ? (
                <Field label={t("vehicles.owner_lookup")} hint={t("vehicles.owner_lookup_hint")}>
                  <input value={form.owner_lookup} onChange={(e) => set({ owner_lookup: e.target.value })} />
                </Field>
              ) : null}
              {form.owner_kind === "person" || form.owner_kind === "organization" ? (
                <div className="row">
                  <Field label={t(`vehicles.owner_name_${form.owner_kind}`)}>
                    <input value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} />
                  </Field>
                  <Field label={t("vehicles.owner_contact")}>
                    <input
                      value={form.owner_contact}
                      onChange={(e) => set({ owner_contact: e.target.value })}
                    />
                  </Field>
                </div>
              ) : null}
              <p className="muted small">{t("vehicles.owner_managed_hint")}</p>
            </div>
          )}
        </Card>

        {vehicle ? null : (
        <Card title={t("vehicles.section_accounting")}>
          <div className="row">
            {form.relation !== "client" ? (
            <Field label={t("vehicles.location")}>
              <select value={form.location_id} onChange={(e) => set({ location_id: e.target.value })}>
                <option value="">—</option>
                {(locations.data?.locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </Field>
            ) : null}
            <Field label={t("vehicles.odometer_unit")}>
              <select value={form.odometer_unit} onChange={(e) => set({ odometer_unit: e.target.value })}>
                <option value="km">{t("vehicles.unit_km")}</option>
                <option value="mi">{t("vehicles.unit_mi")}</option>
              </select>
            </Field>
          </div>
        </Card>
        )}

        <Card title={t("vehicles.name")}>
          <Field label={t("vehicles.name")} hint={t("vehicles.name_auto_hint")}>
            <input value={name} onChange={(e) => setCustomName(e.target.value)} />
          </Field>
          {customName !== null && customName !== autoName ? (
            <button type="button" className="secondary" onClick={() => setCustomName(null)}>
              {t("vehicles.name_reset")}
            </button>
          ) : null}
          <ErrorMessage error={create.error} />
          <div className="actions">
            <button
              type="submit"
              disabled={
                !name.trim() ||
                (!vehicle && form.relation !== "client" && !form.location_id) ||
                create.pending
              }
            >
              {vehicle ? t("common.save") : t("vehicles.create")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
