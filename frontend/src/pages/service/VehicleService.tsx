import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { ApiError } from "../../api/client";
import { useApi } from "../../api/queries";
import type { MaintenanceRule, ServiceJob, Vehicle } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { useVehicles } from "../vehicles/VehiclesPage";
import { price, useServiceWorks, useSystemWorks } from "./hooks";

type Rules = { unit: string; odometer: string | null; rules: MaintenanceRule[] };

function useRules(vehicleId: string, enabled: boolean) {
  const company = useCompany();
  return useApi<Rules>(
    ["maintenance", company.public_id, vehicleId],
    `/c/${company.public_id}/service/vehicles/${vehicleId}/maintenance`,
    enabled,
  );
}

/** The block «Service and maintenance» of a vehicle card (§ 9.1.1): the way to a service operation
 * of the company or at an external counterparty (§ 16.6.1), the periodic rules with their
 * reminders (§ 9.1.5–9.1.15) and the history of done works (§ 9.1.2). */
export function VehicleService({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const managed = vehicle.relation === "own" || vehicle.relation === "managed";
  return (
    <>
      {vehicle.state === "active" && (can("service.create") || can("service.external")) ? (
        <div className="actions" style={{ marginBottom: 16 }}>
          {can("service.create") ? (
            <Link className="button secondary" to={companyPath(company, `/service/operations/new?vehicle=${vehicle.id}`)}>
              {t("service.new_operation")}
            </Link>
          ) : null}
          {can("service.external") ? (
            <Link
              className="button secondary"
              to={companyPath(company, `/service/operations/new?vehicle=${vehicle.id}&external=1`)}
            >
              {t("service.at_contractor")}
            </Link>
          ) : null}
        </div>
      ) : null}
      {managed && vehicle.state === "active" && can("vehicle.view") ? <Maintenance vehicle={vehicle} /> : null}
      {can("service.view") ? <History vehicleId={vehicle.id} /> : null}
    </>
  );
}

export function dueText(rule: MaintenanceRule, unit: string, t: ReturnType<typeof useTranslation>["t"]): string {
  const parts = [];
  if (rule.status.due_on) parts.push(rule.status.due_on);
  if (rule.status.due_odometer) parts.push(`${Number(rule.status.due_odometer)} ${t(`vehicles.unit_${unit}`)}`);
  return parts.length ? parts.join(" · ") : t("service.due_unknown");
}

function Maintenance({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const can = useCan();
  const rules = useRules(vehicle.id, true);
  const [editing, setEditing] = useState<MaintenanceRule | null>(null);
  const unit = rules.data?.unit ?? "km";
  return (
    <Card title={t("service.maintenance")}>
      <p className="muted small">{t("service.maintenance_hint")}</p>
      {rules.isLoading ? <Loading /> : null}
      {rules.data?.rules.length === 0 ? <p className="muted">{t("service.no_rules")}</p> : null}
      {(rules.data?.rules ?? []).map((rule) => (
        <div key={rule.id} className="list-link">
          <strong>{rule.name}</strong>{" "}
          <span className={`badge ${rule.status.state === "ok" ? "ok" : rule.status.state === "unknown" ? "" : "warn"}`}>
            {t(`service.rule_${rule.status.state}`)}
          </span>
          <div className="small">
            {t("service.every", {
              interval: [
                rule.interval_days ? t("service.days", { count: rule.interval_days }) : null,
                rule.interval_distance ? `${rule.interval_distance} ${t(`vehicles.unit_${unit}`)}` : null,
              ]
                .filter(Boolean)
                .join(` ${t("service.or")} `),
            })}
            {" · "}
            {t("service.due", { when: dueText(rule, unit, t) })}
          </div>
          {can("vehicle.edit") ? (
            <button type="button" className="secondary small" onClick={() => setEditing(rule)}>
              {t("common.edit")}
            </button>
          ) : null}
        </div>
      ))}
      {can("vehicle.edit") ? (
        <>
          <RuleForm key={editing?.id ?? "new"} vehicleId={vehicle.id} unit={unit} rule={editing ?? undefined} onDone={() => setEditing(null)} />
          <CopyRules vehicleId={vehicle.id} />
        </>
      ) : null}
    </Card>
  );
}

/** A system work, a work of the catalog, or only a name; distance and/or days; the base of the
 * count when there is no done work yet (§ 9.1.5–9.1.10). */
function RuleForm({ vehicleId, unit, rule, onDone }: { vehicleId: string; unit: string; rule?: MaintenanceRule; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const system = useSystemWorks();
  const works = useServiceWorks();
  const lang = i18n.language.slice(0, 2);
  const [link, setLink] = useState(rule?.system_work ? `system:${rule.system_work}` : rule?.work_id ? `work:${rule.work_id}` : "");
  const [name, setName] = useState(rule?.name ?? "");
  const [days, setDays] = useState(rule?.interval_days ? String(rule.interval_days) : "");
  const [distance, setDistance] = useState(rule?.interval_distance ? String(rule.interval_distance) : "");
  const [remindDays, setRemindDays] = useState(rule?.remind_days ? String(rule.remind_days) : "");
  const [remindDistance, setRemindDistance] = useState(rule?.remind_distance ? String(rule.remind_distance) : "");
  const [lastOn, setLastOn] = useState(rule?.last_done_on ?? "");
  const [lastOdometer, setLastOdometer] = useState(rule?.last_done_odometer ?? "");
  const [nextOn, setNextOn] = useState(rule?.next_due_on ?? "");
  const [nextOdometer, setNextOdometer] = useState(rule?.next_due_odometer ?? "");
  const save = useCommand<Record<string, unknown>, Rules>(
    rule ? `/c/${company.public_id}/service/maintenance/${rule.id}` : `/c/${company.public_id}/service/vehicles/${vehicleId}/maintenance`,
    rule ? "PATCH" : "POST",
  );
  const archive = useCommand<Record<string, unknown>, Rules>(`/c/${company.public_id}/service/maintenance/${rule?.id ?? ""}`, "PATCH");
  const unitLabel = t(`vehicles.unit_${unit}`);

  function pick(value: string) {
    setLink(value);
    if (name.trim()) return;
    const [kind, id] = value.split(":");
    if (kind === "system") {
      const found = system.data?.system_works.find((w) => w.code === id);
      if (found) setName(found.names[lang] ?? found.names.en ?? "");
    } else if (kind === "work") {
      setName(works.data?.works.find((w) => w.id === id)?.name ?? "");
    }
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["maintenance"] });
    await queryClient.invalidateQueries({ queryKey: ["maintenance-due"] });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const [kind, id] = link.split(":");
    const body: Record<string, unknown> = {
      name,
      system_work: kind === "system" ? id : null,
      work_id: kind === "work" ? id : null,
      interval_days: days || null,
      interval_distance: distance || null,
      remind_days: remindDays || null,
      remind_distance: remindDistance || null,
      last_done_on: lastOn || null,
      last_done_odometer: lastOdometer || null,
      next_due_on: nextOn || null,
      next_due_odometer: nextOdometer || null,
    };
    if (rule) body.expected_version = rule.version;
    const result = await save.run(body);
    if (result) {
      await refresh();
      onDone();
    }
  }

  return (
    <form className="stack" onSubmit={submit} style={{ marginTop: 12 }}>
      <h3>{rule ? t("service.edit_rule") : t("service.new_rule")}</h3>
      <div className="row">
        <Field label={t("service.rule_work")} hint={t("service.rule_work_hint")}>
          <select value={link} onChange={(e) => pick(e.target.value)}>
            <option value="">{t("service.rule_own")}</option>
            <optgroup label={t("service.system_work")}>
              {(system.data?.system_works ?? []).map((w) => (
                <option key={w.code} value={`system:${w.code}`}>
                  {w.names[lang] ?? w.names.en}
                </option>
              ))}
            </optgroup>
            <optgroup label={t("service.catalog")}>
              {(works.data?.works ?? [])
                .filter((w) => !w.system_work)
                .map((w) => (
                  <option key={w.id} value={`work:${w.id}`}>
                    {w.name}
                  </option>
                ))}
            </optgroup>
          </select>
        </Field>
        <Field label={t("tariffs.name")}>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
      </div>
      <div className="row">
        <Field label={t("service.interval_days")}>
          <input inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} />
        </Field>
        <Field label={t("service.interval_distance", { unit: unitLabel })}>
          <input inputMode="numeric" value={distance} onChange={(e) => setDistance(e.target.value)} />
        </Field>
        <Field label={t("service.remind_days")}>
          <input inputMode="numeric" value={remindDays} onChange={(e) => setRemindDays(e.target.value)} />
        </Field>
        <Field label={t("service.remind_distance", { unit: unitLabel })}>
          <input inputMode="numeric" value={remindDistance} onChange={(e) => setRemindDistance(e.target.value)} />
        </Field>
      </div>
      <p className="muted small">{t("service.rule_base_hint")}</p>
      <div className="row">
        <Field label={t("service.last_done_on")}>
          <input type="date" value={lastOn} onChange={(e) => setLastOn(e.target.value)} />
        </Field>
        <Field label={t("service.last_done_odometer", { unit: unitLabel })}>
          <input inputMode="decimal" value={lastOdometer} onChange={(e) => setLastOdometer(e.target.value)} />
        </Field>
        <Field label={t("service.next_due_on")}>
          <input type="date" value={nextOn} onChange={(e) => setNextOn(e.target.value)} />
        </Field>
        <Field label={t("service.next_due_odometer", { unit: unitLabel })}>
          <input inputMode="decimal" value={nextOdometer} onChange={(e) => setNextOdometer(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={save.error ?? archive.error} />
      <div className="actions">
        <button type="submit" disabled={save.pending || !name.trim()}>
          {rule ? t("common.save") : t("service.add_rule")}
        </button>
        {rule ? (
          <>
            <button
              type="button"
              className="secondary"
              disabled={archive.pending}
              onClick={() =>
                void archive.run({ archived: true, expected_version: rule.version }).then(async (r) => {
                  if (r) {
                    await refresh();
                    onDone();
                  }
                })
              }
            >
              {t("service.archive")}
            </button>
            <button type="button" className="secondary" onClick={onDone}>
              {t("common.cancel")}
            </button>
          </>
        ) : null}
      </div>
    </form>
  );
}

type Conflict = { rule: string; name: string; existing: string };

/** Rules of another vehicle without its history; a matching rule is kept or replaced only by
 * an explicit choice (§ 9.1.12). */
function CopyRules({ vehicleId }: { vehicleId: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const queryClient = useQueryClient();
  const vehicles = useVehicles();
  const [source, setSource] = useState("");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const copy = useCommand<Record<string, unknown>, Rules>(`/c/${company.public_id}/service/vehicles/${vehicleId}/maintenance/copy`);
  const conflicts = conflictsOf(copy.error);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const result = await copy.run({ from_vehicle_id: source, choices });
    if (result) {
      setChoices({});
      await queryClient.invalidateQueries({ queryKey: ["maintenance"] });
    }
  }
  return (
    <form className="stack" onSubmit={submit} style={{ marginTop: 12 }}>
      <h3>{t("service.copy_rules")}</h3>
      <div className="row">
        <Field label={t("service.copy_from")}>
          <select value={source} onChange={(e) => { setSource(e.target.value); setChoices({}); }}>
            <option value="">—</option>
            {(vehicles.data?.vehicles ?? [])
              .filter((v) => v.id !== vehicleId && (v.relation === "own" || v.relation === "managed"))
              .map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
          </select>
        </Field>
      </div>
      {conflicts.map((c) => (
        <div key={c.rule} className="small">
          {t("service.copy_conflict", { name: c.name })}{" "}
          <label>
            <input type="radio" checked={choices[c.rule] === "keep"} onChange={() => setChoices({ ...choices, [c.rule]: "keep" })} />{" "}
            {t("service.keep_existing")}
          </label>{" "}
          <label>
            <input type="radio" checked={choices[c.rule] === "replace"} onChange={() => setChoices({ ...choices, [c.rule]: "replace" })} />{" "}
            {t("service.replace_existing")}
          </label>
        </div>
      ))}
      {conflicts.length === 0 ? <ErrorMessage error={copy.error} /> : null}
      <div className="actions">
        <button type="submit" className="secondary" disabled={copy.pending || !source}>
          {t("service.copy")}
        </button>
      </div>
    </form>
  );
}

function conflictsOf(error: ApiError | null): Conflict[] {
  if (error?.code !== "MAINTENANCE_COPY_CONFLICTS") return [];
  const found = (error.params as { conflicts?: Conflict[] } | undefined)?.conflicts;
  return found ?? [];
}

function History({ vehicleId }: { vehicleId: string }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const history = useApi<{ jobs: (ServiceJob & { operation_number: number })[] }>(
    ["vehicle-service-history", company.public_id, vehicleId],
    `/c/${company.public_id}/service/vehicles/${vehicleId}/history`,
  );
  const zone = company.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const jobs = history.data?.jobs ?? [];
  return (
    <Card title={t("service.history")}>
      {history.isLoading ? <Loading /> : null}
      {history.data && jobs.length === 0 ? <p className="muted">{t("service.no_history")}</p> : null}
      {jobs.length ? (
        <table>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{formatMoment(j.actual_end, zone, i18n.language)}</td>
                <td>
                  <Link to={companyPath(company, `/service/operations/${j.operation_id}`)}>{j.name}</Link>{" "}
                  {j.state === "terminated" ? <span className="badge">{t("service.job_terminated")}</span> : null}
                </td>
                <td>{j.odometer ? Number(j.odometer) : "—"}</td>
                <td>{j.contractor_name ?? j.executor_name ?? "—"}</td>
                <td className="muted small">{price(j.price, j.currency, t)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}
