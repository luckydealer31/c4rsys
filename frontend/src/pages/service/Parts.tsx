import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { ContractorMaterial, PartInstallation, ServiceJob, ServiceMaterial, ServiceOperation, Storage } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { fromLocalInput, toLocalInput } from "../../time";
import { OPERATION_KEYS } from "./hooks";

function useRefresh() {
  const queryClient = useQueryClient();
  return async () => {
    for (const key of [...OPERATION_KEYS, "vehicle-parts", "warehouse-removed-parts", "warehouse-reserves"]) {
      await queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

export function useWarehouseChoices() {
  const company = useCompany();
  const storages = useApi<{ storages: Storage[] }>(
    ["warehouse-storages", company.public_id],
    `/c/${company.public_id}/warehouse/storages`,
  );
  return (storages.data?.storages ?? []).filter((s) => s.state === "enabled" && !s.location_deactivated);
}

export function partLabel(p: PartInstallation) {
  return `${p.item.name} ${p.asset.public_id}`;
}

/** The state of a material of a work in block 15: planned with its reserve, issued, installed;
 * the actions on it (§ 11.7.7, § 11.4.3) and the purchase cost of the consumed (§ 11.2.9). */
export function MaterialExtras({ op, material, zone }: { op: ServiceOperation; material: ServiceMaterial; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const [installing, setInstalling] = useState(false);
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [odometer, setOdometer] = useState("");
  const [replaces, setReplaces] = useState(material.replaces_id ?? "");
  const command = useCommand<Record<string, unknown>, unknown>(
    (b) => `/c/${company.public_id}/service/materials/${material.id}/${String(b.step)}`,
  );
  async function run(step: string, body: Record<string, unknown> = {}) {
    if ((await command.run({ step, expected_version: material.version, ...body })) !== null) {
      setInstalling(false);
      await refresh();
    }
  }
  const installed = op.installations.find((i) => i.id === material.installation_id);
  const replaced = op.installed_parts.find((i) => i.id === material.replaces_id) ?? op.installations.find((i) => i.id === material.replaces_id);
  const others = op.installed_parts.filter((p) => p.id !== material.installation_id);
  return (
    <div className="small">
      {material.state === "planned" ? (
        <>
          <span className="badge">{t("parts.material_planned")}</span>{" "}
          <span className={`badge${material.reserve_state === "active" ? " ok" : ""}`}>
            {material.reserve_state === "active" ? t("parts.reserved") : t("parts.not_reserved")}
          </span>{" "}
          {can("warehouse.issue") ? (
            <button type="button" className="link" disabled={command.pending} onClick={() => void run("issue")}>
              {t("parts.issue_planned")}
            </button>
          ) : null}{" "}
          {material.reserve_state !== "active" && (can("warehouse.reserve") || can("warehouse.issue")) ? (
            <button type="button" className="link" disabled={command.pending} onClick={() => void run("reserve")}>
              {t("parts.reserve_planned")}
            </button>
          ) : null}{" "}
          {can("service.create") ? (
            <button type="button" className="link" disabled={command.pending} onClick={() => void run("take-off", { reason: "" })}>
              {t("parts.take_off")}
            </button>
          ) : null}
        </>
      ) : null}
      {material.state === "removed" ? <span className="muted">{t("parts.material_removed")}</span> : null}
      {material.state === "issued" && material.cost !== null && Number(material.consumed) > 0 ? (
        <span>
          {t("parts.material_cost", { cost: material.cost })}
          {material.cost_known ? null : <span className="badge warn" style={{ marginLeft: 6 }}>{t("parts.incomplete")}</span>}
        </span>
      ) : null}{" "}
      {replaced ? <span>{t("parts.replaces_value", { part: partLabel(replaced) })}</span> : null}{" "}
      {installed ? <span className="badge ok">{t("parts.installed_badge")}</span> : null}
      {material.state === "issued" && material.individual && !installed && can("service.work") ? (
        <button type="button" className="link" onClick={() => setInstalling(!installing)}>
          {t("parts.install")}
        </button>
      ) : null}
      {installing ? (
        <div className="row">
          <Field label={t("parts.installed_at")}>
            <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          </Field>
          <Field label={t("parts.odometer")} hint={t("parts.odometer_hint")}>
            <input inputMode="decimal" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
          </Field>
          <Field label={t("parts.replaces")} hint={t("parts.replaces_hint")}>
            <select value={replaces} onChange={(e) => setReplaces(e.target.value)}>
              <option value="">{t("parts.replaces_none")}</option>
              {others.map((p) => (
                <option key={p.id} value={p.id}>
                  {partLabel(p)}
                </option>
              ))}
            </select>
          </Field>
          <div className="actions">
            <button
              type="button"
              disabled={!at || command.pending}
              onClick={() =>
                void run("install", { installed_at: fromLocalInput(at, zone), odometer: odometer || null, replaces_id: replaces || null })
              }
            >
              {t("parts.install")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={command.error} />
    </div>
  );
}

/** «Replaces the installed part» chosen when a part is added to a work (§ 11.4.3). */
export function ReplacesPicker({ op, value, onChange }: { op: ServiceOperation; value: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  if (!op.installed_parts.length) return null;
  return (
    <Field label={t("parts.replaces")} hint={t("parts.replaces_hint")}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("parts.replaces_none")}</option>
        {op.installed_parts.map((p) => (
          <option key={p.id} value={p.id}>
            {partLabel(p)}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** A part installed on the vehicle is removed by this work with its actual moment and odometer;
 * a missed removal is entered later the same way (§ 11.4.3). */
export function RemovePart({ op, job, zone }: { op: ServiceOperation; job: ServiceJob; zone: string }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState("");
  const [at, setAt] = useState(toLocalInput(new Date(), zone));
  const [odometer, setOdometer] = useState("");
  const [comment, setComment] = useState("");
  const remove = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/service/jobs/${job.id}/remove-part`);
  if (!op.installed_parts.length || !can("service.work") || job.state === "cancelled") return null;
  if (!open) {
    return (
      <button type="button" className="secondary small" onClick={() => setOpen(true)}>
        {t("parts.remove")}
      </button>
    );
  }
  return (
    <div className="line-card">
      <h3>{t("parts.remove_title")}</h3>
      <div className="row">
        <Field label={t("parts.part")}>
          <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
            <option value="">—</option>
            {op.installed_parts.map((p) => (
              <option key={p.id} value={p.id}>
                {partLabel(p)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("parts.removed_at")}>
          <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
        </Field>
        <Field label={t("parts.odometer")} hint={t("parts.odometer_hint")}>
          <input inputMode="decimal" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
        </Field>
      </div>
      <Field label={t("reserves.comment")}>
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
      <ErrorMessage error={remove.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!chosen || !at || remove.pending}
          onClick={async () => {
            const result = await remove.run({
              expected_version: job.version,
              installation_id: chosen,
              removed_at: fromLocalInput(at, zone),
              odometer: odometer || null,
              comment,
            });
            if (result !== null) {
              setOpen(false);
              await refresh();
            }
          }}
        >
          {t("parts.remove")}
        </button>
        <button type="button" className="secondary" onClick={() => setOpen(false)}>
          {t("common.cancel")}
        </button>
      </div>
      <p className="muted small">{t("parts.remove_hint")}</p>
    </div>
  );
}

/** Parts and materials an external service used from its own stock, with their cost, without a
 * receipt into the warehouse (§ 16.5.3). */
export function OwnMaterials({ op, job }: { op: ServiceOperation; job: ServiceJob }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh();
  const external = Boolean(job.contractor_id || op.counterparty_id);
  const items = op.own_materials.filter((m) => m.job_id === job.id);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState("");
  const [cost, setCost] = useState("");
  const add = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/service/jobs/${job.id}/own-materials`);
  const remove = useCommand<{ id: string; expected_version: number; reason: string }, unknown>(
    (b) => `/c/${company.public_id}/service/own-materials/${b.id}/remove`,
  );
  if (!external || (!items.length && !can("service.create"))) return null;
  return (
    <div className="small" style={{ marginTop: 8 }}>
      <strong>{t("parts.own_title")}</strong>
      {items.map((m: ContractorMaterial) => (
        <div key={m.id} className={m.removed_at ? "muted" : ""}>
          {m.name} · {Number(m.quantity)} {m.unit} ·{" "}
          {m.cost === null ? t("receipts.cost_not_given") : `${Number(m.cost)} ${m.currency}`}
          {m.removed_at ? ` · ${t("parts.own_removed", { reason: m.remove_reason })}` : null}{" "}
          {!m.removed_at && can("service.create") ? (
            <button
              type="button"
              className="link"
              onClick={async () => {
                const reason = window.prompt(t("parts.own_remove_reason"));
                if (reason && (await remove.run({ id: m.id, expected_version: m.version, reason })) !== null) await refresh();
              }}
            >
              {t("parts.own_remove")}
            </button>
          ) : null}
        </div>
      ))}
      {can("service.create") ? (
        open ? (
          <div className="row">
            <Field label={t("parts.own_name")}>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t("operations.quantity")}>
              <input inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </Field>
            <Field label={t("warehouse.unit")}>
              <input value={unit} onChange={(e) => setUnit(e.target.value)} />
            </Field>
            <Field label={`${t("parts.cost")}, ${job.currency}`} hint={t("parts.own_cost_hint")}>
              <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} />
            </Field>
            <div className="actions">
              <button
                type="button"
                disabled={!name.trim() || !quantity || !unit.trim() || add.pending}
                onClick={async () => {
                  const result = await add.run({
                    expected_version: job.version,
                    name,
                    quantity: quantity.replace(",", "."),
                    unit,
                    cost: cost ? cost.replace(",", ".") : null,
                  });
                  if (result !== null) {
                    setOpen(false);
                    setName("");
                    setQuantity("");
                    setUnit("");
                    setCost("");
                    await refresh();
                  }
                }}
              >
                {t("parts.own_add")}
              </button>
            </div>
          </div>
        ) : (
          <div>
            <button type="button" className="link" onClick={() => setOpen(true)}>
              {t("parts.own_add")}
            </button>
          </div>
        )
      ) : null}
      <ErrorMessage error={add.error ?? remove.error} />
    </div>
  );
}
