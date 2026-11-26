import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import { ApiError, uploadPages } from "../../api/client";
import { useApi } from "../../api/queries";
import type { CatalogMake, CatalogModel, Vehicle, VehicleOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Info, Loading } from "../../components/ui";
import { NO_OWNER, OwnerCardPicker, ownerBody, type OwnerValue } from "./OwnerCardPicker";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { isMini } from "../../mini";
import { countries } from "../settings/CompanyProfilePage";
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
  owner: OwnerValue;
  odometer_unit: string;
  location_id: string;
};

/** What is read from a paper of a vehicle: an empty text or null — not read. */
type Paper = Record<string, string | number | boolean | null>;
/** A document of the card offered after a paper is read. */
type ReadDocument = {
  on: boolean;
  name: string;
  number: string;
  issued_by: string;
  valid_from: string;
  file_id: string;
};
// What the steps do not ask: read from the paper, it is written into the card with the rest.
const PAPER_MORE = [
  "plate_region",
  "plate_country",
  "color",
  "body_number",
  "chassis_number",
  "frame_number",
  "engine_number",
  "body_type",
  "fuel_type",
  "transmission",
  "drive",
  "engine_volume_cc",
  "power_hp",
  "seats",
  "doors",
  "curb_weight_kg",
  "gross_weight_kg",
  "power_kw",
  "vehicle_category",
  "eco_class",
  "type_approval",
  "special_marks",
] as const;
const PAPER_WHOLE = new Set<string>(["engine_volume_cc", "power_hp", "seats", "doors", "curb_weight_kg", "gross_weight_kg"]);
// The lists of values and where their names are.
const PAPER_NAMES: Record<string, string> = {
  body_type: "vehicles.body",
  fuel_type: "vehicles.fuel",
  transmission: "vehicles.gearbox",
  drive: "vehicles.drives",
};


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
    owner: NO_OWNER,
    odometer_unit: "km",
    location_id: "",
  });
  const [customName, setCustomName] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const create = useCommand<Record<string, unknown>, { vehicle: Vehicle; warnings: string[] }>(
    `/c/${company.public_id}/vehicles`,
    "POST",
  );
  // A vehicle added as managed that the company already keeps as a client one is not doubled
  // (customer's decision of 23.11.2026): the earlier card is taken into management.
  const take = useCommand<Record<string, unknown>, unknown>(
    (body) => `/c/${company.public_id}/vehicles/${String(body.card_id)}/take-into-management`,
    "POST",
  );
  const set = (changes: Partial<Form>) => setForm({ ...form, ...changes });
  // A paper of the vehicle — a registration certificate, a blue book — is read into the steps
  // (customer's decision of 22.11.2026): what the steps ask is put into them, the rest is shown
  // to be checked and is written with the card. Nothing is made until the last step.
  const reading = useCommand<{ document_id: string }, Paper>(`/c/${company.public_id}/vehicles/recognize`);
  const [uploading, setUploading] = useState(false);
  const [more, setMore] = useState<Record<string, string> | null>(null);
  const [nothing, setNothing] = useState(false);
  const [ownerRead, setOwnerRead] = useState("");
  const [documents, setDocuments] = useState<ReadDocument[]>([]);
  const [refused, setRefused] = useState<ApiError | null>(null);
  async function readPaper(files: File[]) {
    setNothing(false);
    setRefused(null);
    setUploading(true);
    let id = "";
    try {
      id = (await uploadPages("vehicle_paper", files)).id;
    } catch (error) {
      if (error instanceof ApiError) setRefused(error);
      return;
    } finally {
      setUploading(false);
    }
    await fill(id);
  }
  /** The steps are filled from a paper already kept: one uploaded here, or one sent to the
   * Telegram bot, whose button «Check on the site» leads here with `?paper=` (customer's decision
   * of 22.11.2026). */
  async function fill(id: string) {
    const paper = await reading.run({ document_id: id });
    if (!paper) return;
    const text = (key: string) => (paper[key] === null || paper[key] === undefined ? "" : String(paper[key]));
    const type = text("catalog_type") || text("vehicle_type") || form.vehicle_type;
    const inCatalog = text("make_code") !== "" && (options.data?.catalog_types ?? []).includes(type);
    const step: Partial<Form> = { vehicle_type: type };
    if (text("make")) {
      step.make = inCatalog ? text("make_code") : OTHER;
      step.make_other = inCatalog ? "" : text("make");
      step.model = inCatalog ? text("model_code") || (text("model") ? OTHER : "") : "";
      step.model_other = inCatalog && text("model_code") ? "" : text("model");
    }
    for (const key of ["year", "vin", "plate"] as const) if (text(key)) step[key] = text(key);
    // The owner named in the paper is put into the step of the owner (customer's decision of
    // 22.11.2026): it is written for a managed or a client vehicle, not for an own one.
    if (text("owner_name")) {
      // A person apart when the paper tells the parts; else the name as read goes to the last
      // name, to be split by hand (agent's choice, not confirmed).
      const company = text("owner_kind") === "organization";
      const parts = text("owner_last_name") || text("owner_first_name") || text("owner_middle_name");
      step.owner = {
        ...NO_OWNER,
        party: company ? "company" : "person",
        company_name: company ? text("owner_name") : "",
        last_name: company ? "" : parts ? text("owner_last_name") : text("owner_name"),
        first_name: company || !parts ? "" : text("owner_first_name"),
        middle_name: company || !parts ? "" : text("owner_middle_name"),
        address: text("owner_address"),
      };
    }
    // The documents of the card made with the vehicle (customer's decision of 23.11.2026): the
    // paper itself, with its file, and the vehicle passport the paper names.
    const made: ReadDocument[] = [
      {
        on: true,
        name: text("paper_name") || t("vehicles.paper_document"),
        number: text("paper_number"),
        issued_by: text("paper_issued_by"),
        valid_from: text("paper_issued_on"),
        file_id: paper.paper_keepable ? id : "",
      },
    ];
    if (text("passport_number")) {
      made.push({
        on: true,
        name: t("vehicles.paper_passport"),
        number: text("passport_number"),
        issued_by: "",
        valid_from: "",
        file_id: "",
      });
    }
    setDocuments(made);
    setOwnerRead(text("owner_name"));
    const rest = Object.fromEntries(PAPER_MORE.filter((key) => text(key)).map((key) => [key, text(key)]));
    setForm({ ...form, ...step });
    setCustomName(null);
    setMore(rest);
    setNothing(Object.keys(rest).length === 0 && !text("owner_name") && !text("make") && !text("year") && !text("vin") && !text("plate"));
  }

  const fromBot = params.get("paper") ?? "";
  const filled = useRef(false);
  useEffect(() => {
    if (!fromBot || filled.current || !options.data?.paper_recognition) return;
    filled.current = true;
    void fill(fromBot);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the lists are loaded
  }, [fromBot, options.data]);

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
    // The owner is a card of the company (customer's decisions of 25.11.2026).
    if (form.relation !== "own") Object.assign(body, ownerBody(form.relation, form.owner));
    if (usesCatalogMake) {
      body.make = form.make;
      if (form.model && form.model !== OTHER) body.model = form.model;
      else body.model_other = form.model_other;
    } else {
      body.make_other = form.make_other;
      body.model_other = form.model_other;
    }
    for (const [key, value] of Object.entries(more ?? {})) {
      // A body of another type is not sent: the type may have been changed after the reading.
      if (key === "body_type" && !(options.data?.body_types_by_type[form.vehicle_type] ?? []).includes(value)) continue;
      if (value.trim() !== "") body[key] = PAPER_WHOLE.has(key) ? Number(value) : value.trim();
    }
    if (more) {
      body.documents = documents
        .filter((d) => d.on && d.name.trim() !== "")
        .map((d) => ({
          name: d.name.trim(),
          number: d.number.trim(),
          issued_by: d.issued_by.trim(),
          valid_from: d.valid_from || null,
          file_id: d.file_id || null,
          file_name: d.file_id ? d.name.trim() : "",
        }));
    }
    const result = await create.run(body);
    if (result !== null) {
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
      navigate(companyPath(company, `/vehicles/${result.vehicle.id}`));
    }
  }

  const earlier =
    create.error?.code === "VEHICLE_WAS_CLIENT"
      ? (create.error.params as { card_id: string; name: string; version: number })
      : null;
  async function takeEarlier() {
    if (!earlier) return;
    const body: Record<string, unknown> = {
      card_id: earlier.card_id,
      location_id: form.location_id,
      reason: t("vehicles.was_client_reason"),
      expected_version: earlier.version,
      ...ownerBody("managed", form.owner),
    };
    if ((await take.run(body)) !== null) {
      await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
      navigate(companyPath(company, `/vehicles/${earlier.card_id}`));
    }
  }
  const here =
    create.error?.code === "VEHICLE_ALREADY_HERE"
      ? (create.error.params as { card_id: string; name: string; state: string })
      : null;
  const failure = here ? (
    // The same vehicle is not kept twice by one company (customer's decision of 23.11.2026).
    <div className="stack" data-testid="already-here">
      {/* A card in the archive is offered to be restored (customer's decision of 24.11.2026). */}
      <p>{t(here.state === "archived" ? "vehicles.already_archived" : "vehicles.already_here", { name: here.name })}</p>
      <p>
        <Link
          to={companyPath(
            company,
            `/vehicles/${here.card_id}${here.state === "archived" ? "?tab=settings&part=card" : ""}`,
          )}
        >
          {t(here.state === "archived" ? "vehicles.already_archived_open" : "vehicles.already_here_open")}
        </Link>
      </p>
    </div>
  ) : earlier ? (
    <div className="stack" data-testid="was-client">
      <p>{t("vehicles.was_client", { name: earlier.name })}</p>
      {can("vehicle.relation") ? (
        <div className="actions">
          <button type="button" disabled={take.pending} onClick={() => void takeEarlier()}>
            {t("vehicles.take_into_management_button")}
          </button>
        </div>
      ) : (
        <p className="muted small">{t("vehicles.was_client_no_right")}</p>
      )}
      <ErrorMessage error={take.error} />
    </div>
  ) : (
    <ErrorMessage error={create.error} />
  );

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

  /** What one step asks. In the form made after a paper is read all of them stand together. */
  const part = (step: Step, focus: boolean): ReactNode => {
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
              autoFocus={focus}
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
              autoFocus={focus && !hasCatalog}
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
            <select autoFocus={focus} value={form.model} onChange={(e) => set({ model: e.target.value })}>
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
              autoFocus={focus && !usesCatalogMake}
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
          autoFocus={focus}
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
        <input autoFocus={focus} value={form.vin} onChange={(e) => set({ vin: e.target.value })} />
      </Field>
    );
  } else if (step === "plate") {
    content = (
      <Field label={t("vehicles.plate")}>
        <input autoFocus={focus} value={form.plate} onChange={(e) => set({ plate: e.target.value })} />
      </Field>
    );
  } else if (step === "name") {
    content = (
      <div className="stack">
        <Field
          label={t("vehicles.name")}
          hint={autoName ? t("vehicles.name_auto_hint") : t("vehicles.wizard_name_required")}
        >
          <input autoFocus={focus} value={name} onChange={(e) => setCustomName(e.target.value)} />
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
        {/* Three short choices (customer's remark of 23.11.2026): the company itself by its name,
            a managed vehicle, a client's one. */}
        <div className="owner-choice">
          {relationChoice("own", company.display_name)}
          {relationChoice("managed", t("vehicles.owner_short_managed"))}
          {relationChoice("client", t("vehicles.owner_short_client"))}
          {form.relation === "own" ? null : (
            <Info text={t(`vehicles.owner_${form.relation === "client" ? "client" : "managed"}_hint`)} />
          )}
        </div>
        {form.relation === "own" ? null : (
          <OwnerCardPicker
            relation={form.relation === "client" ? "client" : "managed"}
            required={form.relation === "managed"}
            value={form.owner}
            set={(change) => set({ owner: { ...form.owner, ...change } })}
          />
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
        <select autoFocus={focus} value={form.location_id} onChange={(e) => set({ location_id: e.target.value })}>
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
  return content;
  };

  // After a paper is read everything stands in one form (customer's decision of 23.11.2026):
  // it is checked, corrected and the vehicle is made at once, without the steps.
  if (more && !nothing) {
    const lists: Record<string, string[]> = {
      body_type: o.body_types_by_type[form.vehicle_type] ?? [],
      fuel_type: o.fuel_types,
      transmission: o.transmissions,
      drive: o.drives,
    };
    const change = (key: string, value: string) => setMore({ ...more, [key]: value });
    const complete = name.trim() !== "" && (form.relation === "client" || form.location_id !== "");
    return (
      <div className="page">
        {isMini() ? null : (
          <p>
            <Link to={companyPath(company, "/vehicles")}>← {t("nav.garage")}</Link>
          </p>
        )}
        <form
          className="paper-read"
          onSubmit={(event) => {
            event.preventDefault();
            if (complete && !create.pending) void save();
          }}
        >
          <Card title={t("vehicles.create_title")}>
            <p className="small" role="status">
              {t("vehicles.paper_done")}
            </p>
            <div className="paper-part">
              <p className="small muted">{titles.owner}</p>
              {ownerRead && form.relation === "own" ? (
                <p className="small">
                  {t("vehicles.paper_owner", { name: ownerRead })} <Info text={t("vehicles.paper_owner_hint")} />
                </p>
              ) : null}
              {part("owner", false)}
            </div>
            <p className="small muted">{t("vehicles.paper_form")}</p>
            <div className="paper-facts">
              <Field label={titles.type}>
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
              {(["make", "model", "year", "vin", "plate", "name"] as const).map((one) => (
                <div key={one}>{part(one, false)}</div>
              ))}
            </div>
            <div className="paper-facts">
              {Object.entries(more).filter(([key]) => !lists[key] || lists[key].length > 0).map(([key, value]) => (
                <Field key={key} label={t(`vehicles.${key}`)}>
                  {lists[key] ? (
                    <select value={value} onChange={(e) => change(key, e.target.value)}>
                      <option value="">—</option>
                      {(lists[key] ?? []).map((code) => (
                        <option key={code} value={code}>
                          {t(`${PAPER_NAMES[key]}.${code}`)}
                        </option>
                      ))}
                    </select>
                  ) : key === "plate_country" ? (
                    <select value={value} onChange={(e) => change(key, e.target.value)}>
                      <option value="">—</option>
                      {countries(i18n.language).map((c) => (
                        <option key={c.code} value={c.code}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={value}
                      inputMode={PAPER_WHOLE.has(key) ? "numeric" : key === "power_kw" ? "decimal" : undefined}
                      onChange={(e) => change(key, e.target.value)}
                    />
                  )}
                </Field>
              ))}
            </div>
            <div className="paper-facts">
              <Field label={titles.odometer}>
                <select value={form.odometer_unit} onChange={(e) => set({ odometer_unit: e.target.value })}>
                  {(["km", "mi"] as const).map((unit) => (
                    <option key={unit} value={unit}>
                      {t(`vehicles.unit_${unit}`)}
                    </option>
                  ))}
                </select>
              </Field>
              {form.relation === "client" ? null : <div>{part("location", false)}</div>}
            </div>
            <div className="paper-part">
              <p className="small muted">
                {t("vehicles.documents_title")} <Info text={t("vehicles.paper_documents_hint")} />
              </p>
              {documents.map((d, n) => {
                const edit = (changes: Partial<ReadDocument>) =>
                  setDocuments(documents.map((one, i) => (i === n ? { ...one, ...changes } : one)));
                return (
                  <div key={n} className="paper-document">
                    <label>
                      <input type="checkbox" checked={d.on} onChange={(e) => edit({ on: e.target.checked })} />{" "}
                      {t("vehicles.paper_document_add")}
                      {d.file_id ? ` · ${t("vehicles.paper_document_file")}` : ""}
                    </label>
                    {d.on ? (
                      <div className="paper-facts">
                        <Field label={t("vehicles.document_name")}>
                          <input value={d.name} onChange={(e) => edit({ name: e.target.value })} />
                        </Field>
                        <Field label={t("vehicles.document_number")}>
                          <input value={d.number} onChange={(e) => edit({ number: e.target.value })} />
                        </Field>
                        <Field label={t("vehicles.document_issued_by")}>
                          <input value={d.issued_by} onChange={(e) => edit({ issued_by: e.target.value })} />
                        </Field>
                        <Field label={t("vehicles.document_valid_from")}>
                          <input
                            type="date"
                            value={d.valid_from}
                            onChange={(e) => edit({ valid_from: e.target.value })}
                          />
                        </Field>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <p className="muted small">{t("vehicles.wizard_later")}</p>
            {failure}
            <div className="actions">
              <button type="submit" disabled={!complete || create.pending}>
                {t("vehicles.create")}
              </button>
            </div>
          </Card>
        </form>
      </div>
    );
  }

  const content = part(step, true);

  return (
    <div className="page">
      {isMini() ? null : (
        <p>
          <Link to={companyPath(company, "/vehicles")}>← {t("nav.garage")}</Link>
        </p>
      )}
      <h1>{t("vehicles.create_title")}</h1>
      <p className="muted">
        {t("wizard.step", { n: index + 1, total: steps.length })}
        {name.trim() && step !== "name" ? ` · ${name}` : ""}
      </p>
      {o.paper_recognition ? (
        <div className="paper-reading">
          <label className={`button secondary${uploading || reading.pending ? " disabled" : ""}`}>
            {uploading || reading.pending ? t("vehicles.paper_reading") : t("vehicles.paper_read")}
            <input
              type="file"
              hidden
              multiple
              aria-label={t("vehicles.paper_read")}
              accept="image/png,image/jpeg,image/webp,application/pdf"
              disabled={uploading || reading.pending}
              onChange={(e) => {
                const files = [...(e.target.files ?? [])];
                e.target.value = "";
                if (files.length) void readPaper(files);
              }}
            />
          </label>
          <Info text={t("vehicles.paper_hint")} />
          {nothing ? (
            <span className="small" role="status">
              {t("vehicles.paper_nothing")}
            </span>
          ) : null}
          <ErrorMessage error={refused ?? reading.error} />
        </div>
      ) : null}
      <form onSubmit={next} key={step}>
        <Card title={titles[step]}>
          {content}
          {optional ? <p className="muted small">{t("wizard.optional")}</p> : null}
          {last ? <p className="muted small">{t("vehicles.wizard_later")}</p> : null}
          {last ? failure : null}
          <div className="actions">
            {index > 0 ? (
              <button type="button" className="secondary" onClick={() => setIndex(index - 1)}>
                {t("wizard.back")}
              </button>
            ) : null}
            {optional ? (
              <button type="button" className="secondary" onClick={skip}>
                {t("wizard.skip")}
              </button>
            ) : null}
            <button type="submit" disabled={!ready || create.pending}>
              {last ? t("vehicles.create") : t("wizard.next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}
