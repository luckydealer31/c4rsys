import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { api, type ApiError } from "../../api/client";
import type { ExtraService, Tariff } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { companyPath, useCompany } from "../../layout/CompanyShell";
import { currencies } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import {
  fromRows,
  kindBody,
  KINDS,
  RangesEditor,
  unitShort,
  useTariffs,
  type Kind,
  type RangeRow,
} from "../tariffs/TariffsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { EquipmentPicker } from "./Services";
import { EMPTY_ROW, rowBody, rowProblem, VehiclePricesTable, type VehicleRow } from "../tariffs/VehiclePrices";

/** The frame of a step-by-step creation in the rental section: a question per screen, «Back»,
 * «Skip» where the step is optional, «Next», and the creating button at the last step. */
function Steps({
  title,
  step,
  index,
  total,
  ready,
  pending,
  optional = false,
  wide = false,
  createLabel,
  error,
  onBack,
  onNext,
  children,
}: {
  title: string;
  step: string;
  index: number;
  total: number;
  ready: boolean;
  pending: boolean;
  optional?: boolean;
  wide?: boolean;
  createLabel: string;
  error: ApiError | null;
  onBack: () => void;
  onNext: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const last = index === total - 1;
  return (
    <div className={wide ? "page full" : "page"}>
      <p>
        <Link to={companyPath(company, "/rentals?tab=catalog")}>← {t("rentals.tab_catalog")}</Link>
      </p>
      <h1>{title}</h1>
      <p className="muted">{t("wizard.step", { n: index + 1, total })}</p>
      <form
        key={index}
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          if (ready && !pending) onNext();
        }}
      >
        <Card title={step}>
          {children}
          {optional ? <p className="muted small">{t("wizard.optional")}</p> : null}
          {last ? <ErrorMessage error={error} /> : null}
          <div className="actions">
            {index > 0 ? (
              <button type="button" className="secondary" onClick={onBack}>
                {t("wizard.back")}
              </button>
            ) : null}
            <button type="submit" disabled={!ready || pending}>
              {last ? createLabel : t("wizard.next")}
            </button>
          </div>
        </Card>
      </form>
    </div>
  );
}

function Scope({ scope, onChange }: { scope: string[] | "all"; onChange: (scope: string[] | "all") => void }) {
  const { t } = useTranslation();
  const locations = useLocations();
  return (
    <div className="stack">
      <label style={{ display: "block" }}>
        <input type="radio" name="scope" checked={scope === "all"} onChange={() => onChange("all")} />{" "}
        {t("rights.whole_company")}
      </label>
      <label style={{ display: "block" }}>
        <input type="radio" name="scope" checked={scope !== "all"} onChange={() => onChange([])} />{" "}
        {t("rentals.wizard_some_locations")}
      </label>
      {scope !== "all"
        ? (locations.data?.locations ?? []).map((location) => (
            <label key={location.id} style={{ display: "block", marginLeft: 24 }}>
              <input
                type="checkbox"
                checked={scope.includes(location.id)}
                onChange={() =>
                  onChange(scope.includes(location.id) ? scope.filter((x) => x !== location.id) : [...scope, location.id])
                }
              />{" "}
              {location.name}
            </label>
          ))
        : null}
    </div>
  );
}

function CurrencyStep({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  return (
    <Field label={t("cash.currency")} hint={t("cash.currency_hint")}>
      <select autoFocus value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {currencies().map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** A new tariff step by step (customer's decisions of 11.11.2026): the name, the currency, the
 * unit of the count, the ranges of units without a price, where it is available, and the
 * vehicles — each marked one with its own price in every range, its mileage and its deposit.
 * The unit and the currency stay fixed afterwards (§ 13.1). */
export function NewTariffPage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [index, setIndex] = useState(0);
  const [kind, setKind] = useState<Kind>("from_issue");
  const [boundary, setBoundary] = useState("12:00");
  const [currency, setCurrency] = useState(company.main_currency ?? "");
  const [rows, setRows] = useState<RangeRow[]>([{ from: "1", to: "", rate: "" }]);
  const [scope, setScope] = useState<string[] | "all">("all");
  const [name, setName] = useState("");
  const create = useCommand<Record<string, unknown>, { tariff: Tariff }>(`/c/${company.public_id}/tariffs`);
  const all = useVehicles();
  // All own and managed vehicles: a vehicle moves between locations, so the list is not cut
  // by the availability of the tariff.
  const vehicles = (all.data?.vehicles ?? []).filter(
    (v) => (v.relation === "own" || v.relation === "managed") && v.storage === null && v.state !== "archived",
  );
  const [prices, setPrices] = useState<Record<string, VehicleRow>>({});
  const [created, setCreated] = useState<Tariff | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const bounds = fromRows(rows);
  const steps = ["name", "currency", "unit", "ranges", "scope", "vehicles"] as const;
  const step = steps[index] ?? "name";
  const ready =
    step === "currency"
      ? currency !== ""
      : step === "ranges"
        ? rows.every((row, i) => row.from.trim() !== "" && (i === rows.length - 1 || row.to.trim() !== ""))
        : step === "scope"
          ? scope === "all" || scope.length > 0
          : step === "name"
            ? name.trim() !== ""
            : step === "vehicles"
              ? !vehicles.some((v) => rowProblem(prices[v.id] ?? EMPTY_ROW, bounds))
              : true;

  async function next() {
    if (index < steps.length - 1) return setIndex(index + 1);
    setFailure(null);
    setSaving(true);
    // The tariff first, then the terms of each marked vehicle; a repeated «Create» after a
    // refusal goes on with the tariff already made.
    const tariff =
      created ??
      (await create.run({ name, ranges: bounds, locations: scope, currency, ...kindBody(kind, boundary) }))?.tariff ??
      null;
    if (tariff) {
      setCreated(tariff);
      try {
        for (const vehicle of vehicles) {
          const row = prices[vehicle.id] ?? EMPTY_ROW;
          if (!row.checked) continue;
          await api(`/c/${company.public_id}/tariffs/${tariff.id}/vehicles/${vehicle.id}`, {
            method: "PUT",
            body: rowBody(row, bounds),
            idempotencyKey: crypto.randomUUID(),
          });
        }
        await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
        await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
        navigate(companyPath(company, `/rentals/tariffs/${tariff.id}`));
      } catch (caught) {
        setFailure(caught as ApiError);
      }
    }
    setSaving(false);
  }

  const unitName = kind === "from_issue" || kind === "fixed_boundary" ? "day" : kind;
  const titles = {
    unit: t("tariffs.unit"),
    currency: t("cash.currency"),
    ranges: t("tariffs.ranges"),
    scope: t("tariffs.availability"),
    name: t("tariffs.name"),
    vehicles: t("terms.vehicles_title", { currency }),
  };
  return (
    <Steps
      title={t("tariffs.create_title")}
      step={titles[step]}
      index={index}
      total={steps.length}
      ready={ready}
      pending={create.pending || saving}
      createLabel={t("tariffs.create")}
      error={create.error ?? failure}
      wide={step === "vehicles"}
      onBack={() => setIndex(index - 1)}
      onNext={() => void next()}
    >
      {step === "unit" ? (
        <div className="stack">
          {KINDS.map((k) => (
            <label key={k} style={{ display: "block" }}>
              <input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} />{" "}
              {t(`tariffs.kind_${k}`, { time: "…" })}
            </label>
          ))}
          {kind === "fixed_boundary" ? (
            <Field label={t("tariffs.day_boundary")} hint={t("tariffs.day_boundary_hint")}>
              <input type="time" value={boundary} onChange={(e) => setBoundary(e.target.value)} />
            </Field>
          ) : null}
        </div>
      ) : null}
      {step === "currency" ? <CurrencyStep value={currency} onChange={setCurrency} /> : null}
      {step === "ranges" ? (
        <RangesEditor
          rows={rows}
          onChange={setRows}
          unit={unitName}
        />
      ) : null}
      {step === "vehicles" ? (
        <VehiclePricesTable
          vehicles={vehicles}
          ranges={bounds}
          rows={prices}
          onChange={setPrices}
          unit={unitShort(unitName, t)}
          disabled={created !== null && saving}
        />
      ) : null}
      {step === "scope" ? <Scope scope={scope} onChange={setScope} /> : null}
      {step === "name" ? (
        <Field label={t("tariffs.name")}>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      ) : null}
    </Steps>
  );
}

/** A new extra service of the catalog step by step: how it is priced, whether equipment from
 * the warehouse is handed out with it (§ 13.4.7; decision of 11.11.2026), the currency, the
 * price, where and for what it applies (decision У3; the limits by vehicles and tariffs are
 * optional), the warehouse units of a service with equipment, the name. Pricing, unit, currency
 * and «with equipment» stay fixed afterwards; the units change. */
export function NewExtraServicePage() {
  const { t } = useTranslation();
  const company = useCompany();
  const navigate = useNavigate();
  const client = useQueryClient();
  const vehicles = useVehicles();
  const tariffs = useTariffs();
  const [index, setIndex] = useState(0);
  const [pricing, setPricing] = useState<ExtraService["pricing"]>("once");
  const [unit, setUnit] = useState("day");
  const [currency, setCurrency] = useState(company.main_currency ?? "");
  const [price, setPrice] = useState("");
  const [scope, setScope] = useState<string[] | "all">("all");
  const [vehicleIds, setVehicleIds] = useState<string[]>([]);
  const [tariffIds, setTariffIds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [withEquipment, setWithEquipment] = useState(false);
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const create = useCommand<Record<string, unknown>, { service: ExtraService }>(`/c/${company.public_id}/extra-services`);
  type Step = "pricing" | "equipment" | "currency" | "price" | "scope" | "units" | "name";
  const steps: Step[] = withEquipment
    ? ["pricing", "equipment", "currency", "price", "scope", "units", "name"]
    : ["pricing", "equipment", "currency", "price", "scope", "name"];
  const step = steps[index] ?? "pricing";
  const ready =
    step === "currency"
      ? currency !== ""
      : step === "price"
        ? price.trim() !== ""
        : step === "scope"
          ? scope === "all" || scope.length > 0
          : step === "name"
            ? name.trim() !== ""
            : true;
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  async function next() {
    if (index < steps.length - 1) return setIndex(index + 1);
    const body: Record<string, unknown> = {
      name,
      price: price.trim().replace(",", "."),
      locations: scope,
      vehicles: vehicleIds,
      tariffs: tariffIds,
      pricing,
      currency,
      with_equipment: withEquipment,
    };
    if (withEquipment) body.assets = assetIds;
    if (pricing === "time") body.unit = unit;
    const result = await create.run(body);
    if (result) {
      await client.invalidateQueries({ queryKey: ["extra-services", company.public_id] });
      navigate(companyPath(company, `/rentals/services/${result.service.id}`));
    }
  }

  const titles = {
    pricing: t("services.pricing"),
    equipment: t("services.with_equipment"),
    currency: t("cash.currency"),
    price: t(`services.price_${pricing}`),
    scope: t("tariffs.availability"),
    units: t("services.units_title"),
    name: t("tariffs.name"),
  };
  return (
    <Steps
      title={t("services.create_title")}
      step={titles[step]}
      index={index}
      total={steps.length}
      ready={ready}
      pending={create.pending}
      createLabel={t("services.create")}
      error={create.error}
      optional={step === "units"}
      onBack={() => setIndex(index - 1)}
      onNext={() => void next()}
    >
      {step === "pricing" ? (
        <div className="stack">
          {(["once", "quantity", "time"] as const).map((p) => (
            <label key={p} style={{ display: "block" }}>
              <input type="radio" name="pricing" checked={pricing === p} onChange={() => setPricing(p)} />{" "}
              {t(`services.pricing_${p}`)}
            </label>
          ))}
          {pricing === "time" ? (
            <Field label={t("tariffs.unit")}>
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                {(["minute", "hour", "day", "month"] as const).map((u) => (
                  <option key={u} value={u}>
                    {t(`services.unit_${u}`)}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
      ) : null}
      {step === "equipment" ? (
        <div className="stack">
          <label style={{ display: "block" }}>
            <input type="radio" name="equipment" checked={!withEquipment} onChange={() => setWithEquipment(false)} />{" "}
            {t("services.with_equipment_no")}
          </label>
          <label style={{ display: "block" }}>
            <input type="radio" name="equipment" checked={withEquipment} onChange={() => setWithEquipment(true)} />{" "}
            {t("services.with_equipment_yes")}
          </label>
          <p className="muted small">{t("services.with_equipment_hint")}</p>
        </div>
      ) : null}
      {step === "units" ? <EquipmentPicker value={assetIds} onChange={setAssetIds} /> : null}
      {step === "currency" ? <CurrencyStep value={currency} onChange={setCurrency} /> : null}
      {step === "price" ? (
        <Field label={t(`services.price_${pricing}`)}>
          <input autoFocus value={price} inputMode="decimal" onChange={(e) => setPrice(e.target.value)} />
        </Field>
      ) : null}
      {step === "scope" ? (
        <div className="stack">
          <Scope scope={scope} onChange={setScope} />
          {(vehicles.data?.vehicles ?? []).length ? (
            <div className="small">
              {t("services.only_vehicles")}:
              {(vehicles.data?.vehicles ?? []).map((v) => (
                <label key={v.id} style={{ display: "block", marginLeft: 24 }}>
                  <input type="checkbox" checked={vehicleIds.includes(v.id)} onChange={() => setVehicleIds(toggle(vehicleIds, v.id))} />{" "}
                  {v.name}
                </label>
              ))}
            </div>
          ) : null}
          {(tariffs.data?.tariffs ?? []).length ? (
            <div className="small">
              {t("services.only_tariffs")}:
              {(tariffs.data?.tariffs ?? []).map((x) => (
                <label key={x.id} style={{ display: "block", marginLeft: 24 }}>
                  <input type="checkbox" checked={tariffIds.includes(x.id)} onChange={() => setTariffIds(toggle(tariffIds, x.id))} />{" "}
                  {x.name}
                </label>
              ))}
            </div>
          ) : null}
          <p className="muted small">{t("services.scope_hint")}</p>
        </div>
      ) : null}
      {step === "name" ? (
        <Field label={t("tariffs.name")}>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      ) : null}
    </Steps>
  );
}
