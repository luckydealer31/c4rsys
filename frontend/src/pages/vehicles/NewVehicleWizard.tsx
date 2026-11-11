import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, Client, Vehicle, VehicleOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { clientName } from "../clients/ClientsPage";
import { useLocations } from "../settings/LocationsPage";

const OTHER = "__other";

type Step =
  | "type"
  | "make"
  | "model"
  | "year"
  | "vin"
  | "plate"
  | "name"
  | "owner"
  | "odometer"
  | "location";

type Form = {
  vehicle_type: string;
  make: string;
  make_other: string;
  model: string;
  model_other: string;
  year: string;
  vin: string;
  plate: string;
  relation: string;
  owner_kind: string;
  owner_lookup: string;
  owner_name: string;
  owner_contact: string;
  owner_client_id: string;
  client_owner_kind: string;
  odometer_unit: string;
  location_id: string;
};

const OWNER_KINDS = ["profile", "person", "organization", "none"] as const;
const CLIENT_OWNER_KINDS = ["client", "person", "organization", "none"] as const;

/** Step-by-step creation of a vehicle (customer's decision of 11.11.2026): one question per
 * screen — type, make, model, year, VIN, plate, name, owner, odometer unit, location. Make,
 * model, year, VIN and plate may be skipped; the name is required. The vehicle is saved once,
 * at the last step. Country of registration and the characteristics are filled in later, in
 * the vehicle card. The periodic maintenance (§ 9.1.6) is not asked here: it is
 * set in the vehicle card, for own and managed vehicles (customer's decision of 11.11.2026). */
export function NewVehiclePage() {
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
  const [form, setForm] = useState<Form>({
    vehicle_type: "passenger_car",
    make: "",
    make_other: "",
    model: "",
    model_other: "",
    year: "",
    vin: "",
    plate: "",
    relation: initialRelation === "managed" || initialRelation === "client" ? initialRelation : "own",
    owner_kind: "profile",
    owner_lookup: "",
    owner_name: "",
    owner_contact: "",
    owner_client_id: "",
    client_owner_kind: "client",
    odometer_unit: "km",
    location_id: "",
  });
  const [customName, setCustomName] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const create = useCommand<Record<string, unknown>, { vehicle: Vehicle; warnings: string[] }>(
    `/c/${company.public_id}/vehicles`,
    "POST",
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

  const makeName = usesCatalogMake
    ? (makes.data?.makes.find((m) => m.code === form.make)?.name ?? "")
    : form.make_other.trim();
  const modelName =
    usesCatalogMake && form.model !== OTHER
      ? (models.data?.models.find((m) => m.code === form.model)?.name ?? "")
      : form.model_other.trim();
  const autoName = [makeName, modelName, form.plate.trim()].filter(Boolean).join(" ");
  const name = customName ?? autoName;

  // A client vehicle is not attached to a location (§ 8.1.2), so it has no such step.
  const steps: Step[] = [
    "type",
    "make",
    "model",
    "year",
    "vin",
    "plate",
    "name",
    "owner",
    "odometer",
    ...(form.relation === "client" ? [] : (["location"] as Step[])),
  ];
  const step: Step = steps[Math.min(index, steps.length - 1)] ?? "type";
  const last = step === steps[steps.length - 1];
  const ready =
    step === "name" ? name.trim() !== "" : step === "location" ? form.location_id !== "" : true;

  async function save() {
    const body: Record<string, unknown> = {
      name,
      location_id: form.location_id,
      odometer_unit: form.odometer_unit,
      transport_class: "ground",
      vehicle_type: form.vehicle_type,
      year: form.year.trim() === "" ? undefined : Number(form.year),
      plate: form.plate,
      vin: form.vin,
      relation: form.relation,
    };
    if (form.relation === "client") {
      const kind =
        form.client_owner_kind === "client" && !form.owner_client_id ? "none" : form.client_owner_kind;
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
    const result = await create.run(body);
    if (result !== null) {
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
      navigate(companyPath(company, `/vehicles/${result.vehicle.id}`));
    }
  }

  function next(event: FormEvent) {
    event.preventDefault();
    if (!ready || create.pending) return;
    if (last) void save();
    else setIndex(index + 1);
  }

  /** «Skip» clears what the step has asked and goes on. */
  function skip() {
    if (step === "make") set({ make: "", make_other: "", model: "" });
    if (step === "model") set({ model: "", model_other: "" });
    if (step === "year") set({ year: "" });
    if (step === "vin") set({ vin: "" });
    if (step === "plate") set({ plate: "" });
    setIndex(index + 1);
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
  const makeLabel = (m: CatalogMake) =>
    i18n.language === "ru" && m.name_cyrillic && m.name_cyrillic !== m.name
      ? `${m.name} (${m.name_cyrillic})`
      : m.name;
  const optional = ["make", "model", "year", "vin", "plate"].includes(step);

  const ownerManual = (kind: string) =>
    kind === "person" || kind === "organization" ? (
      <div className="row">
        <Field label={t(`vehicles.owner_name_${kind}`)}>
          <input value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} />
        </Field>
        <Field label={t("vehicles.owner_contact")}>
          <input value={form.owner_contact} onChange={(e) => set({ owner_contact: e.target.value })} />
        </Field>
      </div>
    ) : null;
  const relationChoice = (relation: string, label: string) => (
    <label>
      <input
        type="radio"
        name="relation"
        checked={form.relation === relation}
        onChange={() => set({ relation })}
      />{" "}
      {label}
    </label>
  );

  const titles: Record<Step, string> = {
    type: t("vehicles.vehicle_type"),
    make: t("vehicles.make"),
    model: t("vehicles.model"),
    year: t("vehicles.year"),
    vin: t("vehicles.vin"),
    plate: t("vehicles.plate"),
    name: t("vehicles.name"),
    owner: t("vehicles.section_owner"),
    odometer: t("vehicles.odometer_unit"),
    location: t("vehicles.location"),
  };

  let content: ReactNode = null;
  if (step === "type") {
    content = (
      <div className="stack">
        {o.vehicle_types.map((code) => (
          <label key={code} style={{ display: "block" }}>
            <input
              type="radio"
              name="vehicle_type"
              checked={form.vehicle_type === code}
              onChange={() =>
                set({ vehicle_type: code, make: "", model: "", make_other: "", model_other: "" })
              }
            />{" "}
            {t(`vehicles.type_${code}`)}
          </label>
        ))}
      </div>
    );
  } else if (step === "make") {
    content = (
      <div className="stack">
        {hasCatalog ? (
          <Field label={t("vehicles.make")}>
            <select
              autoFocus
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
          <Field label={t("vehicles.make_other")}>
            <input
              autoFocus={!hasCatalog}
              value={form.make_other}
              onChange={(e) => set({ make_other: e.target.value })}
            />
          </Field>
        ) : null}
        {!hasCatalog ? <p className="muted small">{t("vehicles.wizard_no_catalog")}</p> : null}
      </div>
    );
  } else if (step === "model") {
    content = (
      <div className="stack">
        {usesCatalogMake ? (
          <Field label={t("vehicles.model")}>
            <select autoFocus value={form.model} onChange={(e) => set({ model: e.target.value })}>
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
          <Field label={t("vehicles.model_other")}>
            <input
              autoFocus={!usesCatalogMake}
              value={form.model_other}
              onChange={(e) => set({ model_other: e.target.value })}
            />
          </Field>
        ) : null}
      </div>
    );
  } else if (step === "year") {
    content = (
      <Field label={t("vehicles.year")}>
        <input
          autoFocus
          type="number"
          inputMode="numeric"
          value={form.year}
          onChange={(e) => set({ year: e.target.value })}
        />
      </Field>
    );
  } else if (step === "vin") {
    content = (
      <Field label={t("vehicles.vin")} hint={t("vehicles.vin_hint")}>
        <input autoFocus value={form.vin} onChange={(e) => set({ vin: e.target.value })} />
      </Field>
    );
  } else if (step === "plate") {
    content = (
      <Field label={t("vehicles.plate")}>
        <input autoFocus value={form.plate} onChange={(e) => set({ plate: e.target.value })} />
      </Field>
    );
  } else if (step === "name") {
    content = (
      <div className="stack">
        <Field
          label={t("vehicles.name")}
          hint={autoName ? t("vehicles.name_auto_hint") : t("vehicles.wizard_name_required")}
        >
          <input autoFocus value={name} onChange={(e) => setCustomName(e.target.value)} />
        </Field>
        {customName !== null && customName !== autoName && autoName ? (
          <button type="button" className="secondary" onClick={() => setCustomName(null)}>
            {t("vehicles.name_reset")}
          </button>
        ) : null}
      </div>
    );
  } else if (step === "owner") {
    content = (
      <div className="stack">
        <div className="stack">
          {relationChoice("own", t("vehicles.owner_company", { company: company.display_name }))}
          {relationChoice("managed", t("vehicles.owner_other"))}
          {relationChoice("client", t("vehicles.owner_client"))}
        </div>
        {form.relation === "own" ? (
          <dl className="facts">
            <dt>{t("companies.display_name")}</dt>
            <dd>{company.display_name}</dd>
            <dt>{t("company.legal_name")}</dt>
            <dd>{company.legal_name || "—"}</dd>
            <dt>{t("companies.company_id")}</dt>
            <dd>{company.public_id}</dd>
          </dl>
        ) : form.relation === "client" ? (
          <>
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
            {ownerManual(form.client_owner_kind)}
            <p className="muted small">{t("vehicles.owner_client_hint")}</p>
          </>
        ) : (
          <>
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
            {ownerManual(form.owner_kind)}
            <p className="muted small">{t("vehicles.owner_managed_hint")}</p>
          </>
        )}
      </div>
    );
  } else if (step === "odometer") {
    content = (
      <div className="stack">
        {(["km", "mi"] as const).map((unit) => (
          <label key={unit} style={{ display: "block" }}>
            <input
              type="radio"
              name="odometer_unit"
              checked={form.odometer_unit === unit}
              onChange={() => set({ odometer_unit: unit })}
            />{" "}
            {t(`vehicles.unit_${unit}`)}
          </label>
        ))}
      </div>
    );
  } else {
    content = (
      <Field label={t("vehicles.location")} hint={t("vehicles.wizard_location_hint")}>
        <select autoFocus value={form.location_id} onChange={(e) => set({ location_id: e.target.value })}>
          <option value="">—</option>
          {(locations.data?.locations ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  return (
    <div className="page">
      <p>
        <Link to={companyPath(company, "/vehicles")}>← {t("nav.garage")}</Link>
      </p>
      <h1>{t("vehicles.create_title")}</h1>
      <p className="muted">
        {t("vehicles.wizard_step", { n: index + 1, total: steps.length })}
        {name.trim() && step !== "name" ? ` · ${name}` : ""}
      </p>
      <form onSubmit={next} key={step}>
        <Card title={titles[step]}>
          {content}
          {optional ? <p className="muted small">{t("vehicles.wizard_optional")}</p> : null}
          {last ? <p className="muted small">{t("vehicles.wizard_later")}</p> : null}
          {last ? <ErrorMessage error={create.error} /> : null}
          <div className="actions">
            {index > 0 ? (
              <button type="button" className="secondary" onClick={() => setIndex(index - 1)}>
                {t("vehicles.wizard_back")}
              </button>
            ) : null}
            {optional ? (
              <button type="button" className="secondary" onClick={skip}>
                {t("vehicles.wizard_skip")}
              </button>
            ) : null}
            <button type="submit" disabled={!ready || create.pending}>
              {last ? t("vehicles.create") : t("vehicles.wizard_next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
