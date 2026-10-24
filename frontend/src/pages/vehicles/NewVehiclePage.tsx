import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, Vehicle, VehicleOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
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
};

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
};

/** Full creation card of a vehicle (customer's decisions of 24.10.2026; § 8.1.8–8.1.12): class
 * and type first, then make and model from the catalog (or «Other» as text), identification,
 * characteristics and accounting. The name is formed from the make, the model and the plate
 * until the user writes their own. */
export function NewVehiclePage() {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const navigate = useNavigate();
  const locations = useLocations();
  const options = useApi<VehicleOptions>(["vehicle-options"], "/catalog/vehicle-options");
  const [form, setForm] = useState<Form>(EMPTY);
  const [customName, setCustomName] = useState<string | null>(null);
  const create = useCommand<Record<string, unknown>, { vehicle: Vehicle; warnings: string[] }>(
    `/c/${company.public_id}/vehicles`,
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
    };
    if (usesCatalogMake) {
      body.make = form.make;
      if (form.model && form.model !== OTHER) body.model = form.model;
      else body.model_other = form.model_other;
    } else {
      body.make_other = form.make_other;
      body.model_other = form.model_other;
    }
    const result = await create.run(body);
    if (result !== null) {
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
        <Link to={companyPath(company, "/vehicles")}>← {t("nav.garage")}</Link>
      </p>
      <h1>{t("vehicles.create_title")}</h1>
      <form className="stack" onSubmit={submit}>
        <Card title={t("vehicles.section_kind")}>
          <div className="field">
            <span>{t("vehicles.transport_class")}</span>
            <div className="choice-row">
              {o.transport_classes.map((item) => (
                <label key={item.code} className={item.available ? "" : "muted"}>
                  <input
                    type="radio"
                    name="transport_class"
                    disabled={!item.available}
                    checked={form.transport_class === item.code}
                    onChange={() => set({ transport_class: item.code })}
                  />{" "}
                  {t(`vehicles.class_${item.code}`)}
                  {item.available ? "" : ` — ${t("vehicles.later")}`}
                </label>
              ))}
            </div>
          </div>
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
            <Field label={t("vehicles.vin")} hint={t("vehicles.vin_hint")}>
              <input value={form.vin} onChange={(e) => set({ vin: e.target.value })} />
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

        <Card title={t("vehicles.section_accounting")}>
          <div className="row">
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
            <Field label={t("vehicles.odometer_unit")}>
              <select value={form.odometer_unit} onChange={(e) => set({ odometer_unit: e.target.value })}>
                <option value="km">{t("vehicles.unit_km")}</option>
                <option value="mi">{t("vehicles.unit_mi")}</option>
              </select>
            </Field>
          </div>
          <p className="muted small">{t("vehicles.own_only")}</p>
        </Card>

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
            <button type="submit" disabled={!name.trim() || !form.location_id || create.pending}>
              {t("vehicles.create")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
