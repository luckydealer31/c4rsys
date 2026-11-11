import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useParams } from "react-router";

import { useApi } from "../../api/queries";
import { api, type ApiError } from "../../api/client";
import type { Tariff, TariffRange, VehicleTerms } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading, UnconfiguredConfirm } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { currencies } from "../../time";
import { useLocations } from "../settings/LocationsPage";
import { useVehicles } from "../vehicles/VehiclesPage";
import { EMPTY_ROW, rowBody, rowOf, rowProblem, VehiclePricesTable, type VehicleRow } from "./VehiclePrices";

export function useTariffs() {
  const company = useCompany();
  return useApi<{ tariffs: Tariff[] }>(["tariffs", company.public_id], `/c/${company.public_id}/tariffs`);
}

/** Unit of a tariff as one choice: minute, hour, a day «24 hours from issue», settlement days with
 * a daily boundary, calendar month (§ 13.1.4, § 13.2). */
export function unitText(tariff: { unit: string; day_mode: string | null; day_boundary?: string | null }, t: TFunction) {
  if (tariff.unit === "day" && tariff.day_mode === "fixed_boundary") {
    return t("tariffs.kind_fixed_boundary", { time: tariff.day_boundary ?? "" });
  }
  return t(`tariffs.kind_${tariff.unit === "day" ? "from_issue" : tariff.unit}`);
}

/** Short name of the billable unit: «min», «h», «days», «months». */
export function unitShort(unit: string | null | undefined, t: TFunction) {
  return t(`tariffs.short_${unit ?? "day"}`);
}

/** Tariff catalog: tariffs of any unit with price ranges (§ 13.1–13.2). */
/** The list of tariffs lives in the rental section now (decision of 11.11.2026). */
export function TariffsPage() {
  const company = useCompany();
  return <Navigate to={companyPath(company, "/rentals?tab=catalog")} replace />;
}

/** The ranges as text; with a price where one is given (the prices of a vehicle), else only
 * the bounds — the prices of a tariff belong to its vehicles. */
export function rangesText(ranges: TariffRange[], t: TFunction, unit = "day") {
  const short = unitShort(unit, t);
  return ranges
    .map((r) => {
      const bounds = r.to === null ? t("terms.range_from", { from: r.from, unit: short }) : t("terms.range", { from: r.from, to: r.to, unit: short });
      return r.rate === null ? bounds : `${bounds}: ${Number(r.rate)}`;
    })
    .join("; ");
}

export const KINDS = ["minute", "hour", "from_issue", "fixed_boundary", "month"] as const;
export type Kind = (typeof KINDS)[number];

export function kindBody(kind: Kind, boundary: string): Record<string, unknown> {
  if (kind === "from_issue") return { unit: "day", day_mode: "from_issue" };
  if (kind === "fixed_boundary") return { unit: "day", day_mode: "fixed_boundary", day_boundary: boundary };
  return { unit: kind };
}

export type RangeRow = { from: string; to: string; rate: string };

function toRows(ranges: TariffRange[]): RangeRow[] {
  return ranges.map((r) => ({ from: String(r.from), to: r.to === null ? "" : String(r.to), rate: "" }));
}

/** Range rows; the next «from» follows the previous «to» (§ 13.2.8). The server checks the
 * structure. */
export function RangesEditor({
  rows,
  onChange,
  unit,
}: {
  rows: RangeRow[];
  onChange: (rows: RangeRow[]) => void;
  unit: string;
}) {
  const { t } = useTranslation();
  function set(index: number, key: keyof RangeRow, value: string) {
    const next = rows.map((row, i) => (i === index ? { ...row, [key]: value } : row));
    const following = next[index + 1];
    if (key === "to" && following && /^\d+$/.test(value)) {
      next[index + 1] = { ...following, from: String(Number(value) + 1) };
    }
    onChange(next);
  }
  return (
    <div className="stack">
      <table>
        <thead>
          <tr>
            <th>{t("tariffs.from", { unit: unitShort(unit, t) })}</th>
            <th>{t("tariffs.to", { unit: unitShort(unit, t) })}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>
                <input value={row.from} inputMode="numeric" onChange={(e) => set(index, "from", e.target.value)} />
              </td>
              <td>
                <input
                  value={row.to}
                  inputMode="numeric"
                  placeholder={index === rows.length - 1 ? t("tariffs.no_limit") : ""}
                  onChange={(e) => set(index, "to", e.target.value)}
                />
              </td>
              <td>
                {rows.length > 1 ? (
                  <button type="button" className="secondary" onClick={() => onChange(rows.filter((_, i) => i !== index))}>
                    ×
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("tariffs.ranges_hint")}</p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          className="secondary"
          onClick={() => {
            const last = rows[rows.length - 1];
            onChange([...rows, { from: last?.to ? String(Number(last.to) + 1) : "", to: "", rate: "" }]);
          }}
        >
          {t("tariffs.add_range")}
        </button>
      </div>
    </div>
  );
}

export function fromRows(rows: RangeRow[]) {
  // Bounds only: the price of a range is set for each vehicle (decision of 11.11.2026).
  return rows.map((row) => ({ from: Number(row.from), to: row.to.trim() === "" ? null : Number(row.to) }));
}

function TariffForm({ tariff }: { tariff?: Tariff }) {
  const { t } = useTranslation();
  const company = useCompany();
  const client = useQueryClient();
  const locations = useLocations();
  const [name, setName] = useState(tariff?.name ?? "");
  const [currency, setCurrency] = useState(tariff?.currency ?? company.main_currency ?? "");
  const [rows, setRows] = useState<RangeRow[]>(tariff ? toRows(tariff.ranges) : [{ from: "1", to: "", rate: "" }]);
  const [scope, setScope] = useState<string[] | "all">(
    tariff && !tariff.all_locations ? tariff.locations : "all",
  );
  const [saved, setSaved] = useState(false);
  const [kind, setKind] = useState<Kind>("from_issue");
  const [boundary, setBoundary] = useState("12:00");
  const save = useCommand<Record<string, unknown>, { tariff: Tariff }>(
    tariff ? `/c/${company.public_id}/tariffs/${tariff.id}` : `/c/${company.public_id}/tariffs`,
    tariff ? "PATCH" : "POST",
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = { name, ranges: fromRows(rows), locations: scope };
    if (tariff) body.expected_version = tariff.version;
    else {
      Object.assign(body, kindBody(kind, boundary));
      if (currency) body.currency = currency;
    }
    const result = await save.run(body);
    if (result) {
      setSaved(true);
      if (!tariff) {
        setName("");
        setRows([{ from: "1", to: "", rate: "" }]);
      }
      await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tariff", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <Card title={tariff ? t("tariffs.edit_title") : t("tariffs.create_title")}>
      <form className="stack" onSubmit={submit}>
        <div className="row">
          <Field label={t("tariffs.name")}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("cash.currency")} hint={tariff ? t("tariffs.currency_fixed") : t("cash.currency_hint")}>
            <select value={currency} disabled={Boolean(tariff)} onChange={(e) => setCurrency(e.target.value)}>
              <option value="">—</option>
              {currencies().map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("tariffs.unit")} hint={tariff ? t("tariffs.unit_fixed") : undefined}>
            {tariff ? (
              <input value={unitText(tariff, t)} disabled />
            ) : (
              <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t(`tariffs.kind_${k}`, { time: "…" })}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {!tariff && kind === "fixed_boundary" ? (
            <Field label={t("tariffs.day_boundary")} hint={t("tariffs.day_boundary_hint")}>
              <input type="time" value={boundary} onChange={(e) => setBoundary(e.target.value)} />
            </Field>
          ) : null}
        </div>
        <h3>{t("tariffs.ranges")}</h3>
        <RangesEditor
          rows={rows}
          onChange={setRows}
          unit={tariff ? tariff.unit : kind === "from_issue" || kind === "fixed_boundary" ? "day" : kind}
        />
        <div className="small">
          {t("tariffs.availability")}:{" "}
          <label>
            <input type="checkbox" checked={scope === "all"} onChange={() => setScope(scope === "all" ? [] : "all")} />{" "}
            {t("rights.whole_company")}
          </label>
          {scope !== "all"
            ? (locations.data?.locations ?? []).map((location) => (
                <label key={location.id} style={{ marginLeft: 12 }}>
                  <input
                    type="checkbox"
                    checked={scope.includes(location.id)}
                    onChange={() =>
                      setScope(
                        scope.includes(location.id) ? scope.filter((x) => x !== location.id) : [...scope, location.id],
                      )
                    }
                  />{" "}
                  {location.name}
                </label>
              ))
            : null}
        </div>
        <ErrorMessage error={save.error} />
        {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
        <div className="actions">
          <button type="submit" disabled={!name.trim() || save.pending}>
            {tariff ? t("common.save") : t("tariffs.create")}
          </button>
        </div>
      </form>
    </Card>
  );
}

/** One tariff: change, archive or restore, and its vehicles with mileage and deposit terms
 * (§ 13.1.6, § 13.4.1, § 22.1.8). */
export function TariffPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const tariff = useApi<{ tariff: Tariff }>(["tariff", company.public_id, id], `/c/${company.public_id}/tariffs/${id}`);
  const archive = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/archive`);
  const restore = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/tariffs/${id}/restore`);
  if (tariff.isLoading) return <div className="page"><Loading /></div>;
  const data = tariff.data?.tariff;
  if (!data) return <div className="page"><ErrorMessage error={null} /></div>;

  const current = data;
  async function toggle(confirmed = false) {
    const command = current.state === "active" ? archive : restore;
    const body = { expected_version: current.version, ...(confirmed ? { confirm_unconfigured: true } : {}) };
    if ((await command.run(body)) !== null) {
      await client.invalidateQueries({ queryKey: ["tariff", company.public_id] });
      await client.invalidateQueries({ queryKey: ["tariffs", company.public_id] });
      await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
    }
  }

  return (
    <div className="page">
      <h1>
        {data.name}{" "}
        <span className={`badge ${data.state === "active" ? "ok" : ""}`}>{t(`tariffs.state_${data.state}`)}</span>
      </h1>
      {can("tariff.edit") ? <TariffForm key={data.version} tariff={data} /> : null}
      {can("tariff.archive") ? (
        <div className="actions" style={{ marginBottom: 16 }}>
          <UnconfiguredConfirm error={archive.error} pending={archive.pending} onConfirm={() => void toggle(true)} />
          <button type="button" className="secondary" onClick={() => void toggle()}>
            {data.state === "active" ? t("tariffs.archive") : t("tariffs.restore")}
          </button>
          <ErrorMessage error={archive.error ?? restore.error} />
        </div>
      ) : null}
      <TariffVehicles tariff={data} />
    </div>
  );
}

/** The vehicles of the tariff (customer's decision of 11.11.2026): all own and managed
 * vehicles of the company with a mark; a marked vehicle has its own price in every range, its
 * mileage included and the price over it, its deposit. Saving sets the terms of the marked
 * vehicles and takes the tariff off the unmarked ones. A tariff limited to locations applies to
 * a vehicle only while the vehicle is in one of them. */
function TariffVehicles({ tariff }: { tariff: Tariff }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const client = useQueryClient();
  const terms = useApi<{ vehicles: VehicleTerms[] }>(
    ["tariff-vehicles", company.public_id, tariff.id],
    `/c/${company.public_id}/tariffs/${tariff.id}/vehicles`,
  );
  const vehicles = useVehicles();
  const [edited, setEdited] = useState<Record<string, VehicleRow> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const assigned = new Map((terms.data?.vehicles ?? []).map((item) => [item.vehicle_card_id, item]));
  const list = (vehicles.data?.vehicles ?? []).filter(
    (v) => (v.relation === "own" || v.relation === "managed") && v.storage === null && (v.state !== "archived" || assigned.has(v.id)),
  );
  const rows = edited ?? Object.fromEntries([...assigned].map(([id, item]) => [id, rowOf(item)]));
  const problem = list.some((v) => rowProblem(rows[v.id] ?? EMPTY_ROW, tariff.ranges));
  const editable = can("tariff.edit") && tariff.state === "active";

  async function save() {
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      for (const vehicle of list) {
        const row = rows[vehicle.id] ?? EMPTY_ROW;
        const path = `/c/${company.public_id}/tariffs/${tariff.id}/vehicles/${vehicle.id}`;
        if (row.checked) {
          await api(path, { method: "PUT", body: rowBody(row, tariff.ranges), idempotencyKey: crypto.randomUUID() });
        } else if (assigned.has(vehicle.id)) {
          await api(path, { method: "DELETE", body: {}, idempotencyKey: crypto.randomUUID() });
        }
      }
      setSaved(true);
      setEdited(null);
    } catch (caught) {
      setError(caught as ApiError);
    }
    setPending(false);
    await client.invalidateQueries({ queryKey: ["tariff-vehicles", company.public_id, tariff.id] });
    await client.invalidateQueries({ queryKey: ["readiness", company.public_id] });
  }

  return (
    <Card title={t("terms.vehicles_title", { currency: tariff.currency })}>
      {terms.isLoading || vehicles.isLoading ? <Loading /> : null}
      <VehiclePricesTable
        vehicles={list}
        ranges={tariff.ranges}
        rows={rows}
        onChange={(next) => {
          setSaved(false);
          setEdited(next);
        }}
        unit={unitShort(tariff.unit, t)}
        disabled={!editable}
      />
      <ErrorMessage error={error} />
      {saved ? <div className="alert ok">{t("common.saved")}</div> : null}
      {editable ? (
        <div className="actions">
          <button type="button" disabled={pending || problem || edited === null} onClick={() => void save()}>
            {t("terms.assign")}
          </button>
        </div>
      ) : null}
    </Card>
  );
}
