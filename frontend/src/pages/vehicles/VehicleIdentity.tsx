import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useApi } from "../../api/queries";
import type { Vehicle, VehicleOptions } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment } from "../../time";
import { LinkedDocuments } from "../documents/DocumentsPage";
import { VehicleDamagesCard, VehicleInspectionsCard } from "../inspections/Blocks";
import { VehicleService } from "../service/VehicleService";
import { useLocations } from "../settings/LocationsPage";

function useWhen() {
  const { i18n } = useTranslation();
  const company = useCompany();
  return (value: string | null | undefined) => formatMoment(value ?? null, company.timezone ?? "UTC", i18n.language);
}

function useRefreshVehicle(id: string) {
  const company = useCompany();
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["vehicle", company.public_id] });
    await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
    await client.invalidateQueries({ queryKey: ["vehicle-merges", company.public_id, id] });
  };
}

/** § 8.6.1, § 8.6.4: conflicts of identifiers of the card — the fact of a match and its number
 * only; nothing of the other record. A conflict never blocks the card. */
export function ConflictsCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const items = vehicle.identifier_conflicts ?? [];
  if (!items.length) return null;
  const open = items.filter((c) => c.open && !c.left_at);
  return (
    <Card title={t("vehicles.conflicts_title")}>
      {open.length ? <div className="alert warn">{t("vehicles.conflicts_hint")}</div> : null}
      <ul className="plain">
        {items.map((c) => (
          <li key={c.id} style={{ marginBottom: 8 }}>
            <div>
              <strong>{t("vehicles.conflict_number", { number: c.number })}</strong> · {t(`vehicles.${c.kind}`)} «{c.value}» ·{" "}
              <span className={c.open && !c.left_at ? "badge warn" : "badge"}>
                {c.left_at && c.open ? t("vehicles.conflict_left") : t(`vehicles.conflict_review_${c.review}`)}
              </span>
            </div>
            <div className="muted small">
              {t("vehicles.conflict_detected", { at: when(c.detected_at) })}
              {c.closed_at ? ` · ${t("vehicles.conflict_closed", { at: when(c.closed_at) })}` : ""}
            </div>
            {c.own_cards.length ? (
              <div className="small">
                {t("vehicles.conflict_own_cards")}:{" "}
                {c.own_cards.map((o, i) => (
                  <span key={o.id}>
                    {i ? ", " : ""}
                    <Link to={companyPath(company, `/vehicles/${o.id}`)}>{o.name}</Link>
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** В-12.2: the owner changed shared characteristics after the card's values; the card keeps
 * its values until the company applies the owner's data. */
export function OwnerDataBanner({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefreshVehicle(vehicle.id);
  const apply = useCommand<{ expected_version: number }, { vehicle: Vehicle }>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/owner-data`,
  );
  const fields = vehicle.owner_data_newer ?? [];
  if (!fields.length) return null;
  return (
    <div className="alert warn">
      {t("vehicles.owner_data_newer", {
        fields: fields.map((f) => t(`vehicles.${f}`)).join(", "),
      })}{" "}
      {can("vehicle.edit") && vehicle.state === "active" ? (
        <button
          type="button"
          className="secondary"
          disabled={apply.pending}
          onClick={async () => {
            if ((await apply.run({ expected_version: vehicle.version })) !== null) await refresh();
          }}
        >
          {t("vehicles.owner_data_apply")}
        </button>
      ) : null}
      <ErrorMessage error={apply.error} />
    </div>
  );
}

/** В-12.3: one vehicle in the own or managed fleet of two companies — the cards are linked and
 * the verification decides the dispute; the company sees only the number of the conflict. */
export function OperationalDisputeBanner({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const open = (vehicle.operational_disputes ?? []).filter((d) => d.open);
  if (!open.length) return null;
  return (
    <div className="alert warn">
      {open.map((d) => t("vehicles.operational_dispute_open", { number: d.number })).join(" ")}
    </div>
  );
}

/** § 8.6.2: the verification found one physical vehicle and the company has two cards of it —
 * it merges them itself, choosing the main one. */
export function PendingMergeBanner({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const pending = vehicle.pending_local_merge;
  if (!pending) return null;
  return (
    <div className="alert warn">
      {t("vehicles.pending_merge", { vehicle: pending.vehicle_id })}{" "}
      {pending.cards.map((c, i) => (
        <span key={c.id}>
          {i ? ", " : ""}
          <Link to={companyPath(company, `/vehicles/${c.id}?merge=${vehicle.id}`)}>{c.name}</Link>
        </span>
      ))}
    </div>
  );
}

type Comparison = {
  primary: { id: string; name: string };
  duplicate: { id: string; name: string };
  fields: { key: string; primary: unknown; duplicate: unknown; same: boolean }[];
  operations: { primary: { kind: string; id: string }[]; duplicate: { kind: string; id: string }[] };
  restrictions: { primary: unknown[]; duplicate: unknown[] };
  obstacles: string[];
};

const CHOICE_LABELS: Record<string, string> = {
  vehicle_type: "vehicles.type_",
  body_type: "vehicles.body.",
  fuel_type: "vehicles.fuel.",
  transmission: "vehicles.gearbox.",
  drive: "vehicles.drives.",
};

function useShow() {
  const { t } = useTranslation();
  const company = useCompany();
  const locations = useLocations();
  return (key: string, value: unknown): string => {
    if (value === null || value === undefined || value === "") return "—";
    if (key === "location") return locations.data?.locations.find((l) => l.id === value)?.name ?? String(value);
    if (key === "contexts") return (value as string[]).map((c) => t(`contexts.${c}`)).join(", ") || "—";
    if (key === "acquisition") {
      const a = value as { cost: string; currency: string };
      return `${Number(a.cost)} ${a.currency}`;
    }
    if (key === "owner") {
      const o = value as { kind: string; lookup: string; name: string };
      if (o.kind === "company") return company.display_name;
      return [t(`vehicles.owner_kind_${o.kind}`, { defaultValue: o.kind }), o.lookup, o.name].filter(Boolean).join(" · ");
    }
    const prefix = CHOICE_LABELS[key.replace("characteristic.", "")];
    return prefix ? t(`${prefix}${String(value)}`) : String(value);
  };
}

function fieldLabel(t: ReturnType<typeof useTranslation>["t"], key: string): string {
  if (key.startsWith("characteristic.")) return t(`vehicles.${key.slice("characteristic.".length)}`);
  return t(`vehicles.merge_field_${key}`);
}

/** § 8.6.6: the merge of a duplicate into this card: the comparison, the explicit choice of the
 * different values, a separate right and a repeated one-time code. */
export function MergeCard({ vehicle, preselected }: { vehicle: Vehicle; preselected: string | null }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const show = useShow();
  const refresh = useRefreshVehicle(vehicle.id);
  const candidates = vehicle.merge_candidates ?? [];
  const [duplicate, setDuplicate] = useState(
    preselected && candidates.some((c) => c.id === preselected) ? preselected : "",
  );
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [code, setCode] = useState("");
  const [reason, setReason] = useState("");
  const [sent, setSent] = useState(false);
  const base = `/c/${company.public_id}/vehicles/${vehicle.id}`;
  const preview = useApi<{ comparison: Comparison }>(
    ["vehicle-merge-preview", company.public_id, vehicle.id, duplicate],
    `${base}/merge/preview?duplicate=${duplicate}`,
    Boolean(duplicate),
  );
  const sendCode = useCommand<{ duplicate_id: string }, unknown>(`${base}/merge/code`);
  const merge = useCommand<Record<string, unknown>, unknown>(`${base}/merge`);
  if (!can("vehicle.merge") || vehicle.state !== "active" || !candidates.length) return null;
  const comparison = preview.data?.comparison;
  const differing = comparison?.fields.filter((f) => !f.same) ?? [];
  const chosen = differing.every((f) => choices[f.key]);
  return (
    <Card title={t("vehicles.merge_title")}>
      <p className="muted small">{t("vehicles.merge_hint")}</p>
      <Field label={t("vehicles.merge_duplicate")}>
        <select
          value={duplicate}
          onChange={(e) => {
            setDuplicate(e.target.value);
            setChoices({});
            setSent(false);
          }}
        >
          <option value="">—</option>
          {candidates.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      {preview.isLoading ? <Loading /> : null}
      <ErrorMessage error={preview.error as never} />
      {comparison ? (
        <>
          <table>
            <thead>
              <tr>
                <th>{t("vehicles.merge_field")}</th>
                <th>{t("vehicles.merge_main", { name: comparison.primary.name })}</th>
                <th>{t("vehicles.merge_dup", { name: comparison.duplicate.name })}</th>
              </tr>
            </thead>
            <tbody>
              {comparison.fields
                .filter((f) => !f.same || !f.key.startsWith("characteristic."))
                .map((f) => (
                  <tr key={f.key}>
                    <td>{fieldLabel(t, f.key)}</td>
                    {(["primary", "duplicate"] as const).map((side) => (
                      <td key={side}>
                        {f.same ? (
                          show(f.key, f[side])
                        ) : (
                          <label>
                            <input
                              type="radio"
                              name={`merge-${f.key}`}
                              checked={choices[f.key] === side}
                              onChange={() => setChoices({ ...choices, [f.key]: side })}
                            />{" "}
                            {show(f.key, f[side])}
                          </label>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="muted small">{t("vehicles.merge_kept_hint")}</p>
          <p className="small">
            {t("vehicles.merge_operations", {
              primary: comparison.operations.primary.length,
              duplicate: comparison.operations.duplicate.length,
            })}
          </p>
          {comparison.obstacles.length ? (
            <div className="alert warn">
              {comparison.obstacles.map((o) => t(`vehicles.merge_obstacle_${o}`)).join(" ")}
            </div>
          ) : (
            <>
              {!sent ? (
                <div className="actions">
                  <button
                    type="button"
                    className="secondary"
                    disabled={!chosen || sendCode.pending}
                    onClick={async () => {
                      if ((await sendCode.run({ duplicate_id: duplicate })) !== null) setSent(true);
                    }}
                  >
                    {t("vehicles.merge_send_code")}
                  </button>
                </div>
              ) : (
                <div className="row">
                  <Field label={t("vehicles.merge_code")} hint={t("vehicles.merge_code_hint")}>
                    <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
                  </Field>
                  <Field label={t("vehicles.merge_reason")}>
                    <input value={reason} onChange={(e) => setReason(e.target.value)} />
                  </Field>
                </div>
              )}
              <ErrorMessage error={sendCode.error ?? merge.error} />
              {sent ? (
                <div className="actions">
                  <button
                    type="button"
                    className="danger"
                    disabled={!chosen || code.length < 4 || merge.pending}
                    onClick={async () => {
                      const done = await merge.run({ duplicate_id: duplicate, choices, code, reason });
                      if (done !== null) {
                        setDuplicate("");
                        setSent(false);
                        setCode("");
                        await refresh();
                      }
                    }}
                  >
                    {t("vehicles.merge_submit")}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </>
      ) : null}
    </Card>
  );
}

type Merge = {
  id: string;
  state: "merged" | "split";
  primary: { id: string; name: string };
  duplicate: { id: string; name: string };
  reason: string;
  chosen: string[];
  merged_at: string;
  split_at: string | null;
  split_reason: string;
};
type SplitRecord = { key: string; kind: string; id: string; label: string; at: string; follows: string | null };

/** § 8.6.5: the merges into this card and their split — one action with the manual assignment of
 * the records made after the merge. */
export function MergesCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const merges = useApi<{ merges: Merge[] }>(
    ["vehicle-merges", company.public_id, vehicle.id],
    `/c/${company.public_id}/vehicles/${vehicle.id}/merges`,
  );
  const [splitting, setSplitting] = useState<string | null>(null);
  const items = merges.data?.merges ?? [];
  if (!items.length) return null;
  return (
    <Card title={t("vehicles.merges_title")}>
      <ul className="plain">
        {items.map((m) => (
          <li key={m.id} className="stack" style={{ marginBottom: 8 }}>
            <span>
              «{m.duplicate.name}» → «{m.primary.name}» · {t(`vehicles.merge_state_${m.state}`)} · {when(m.merged_at)}
              {m.split_at ? ` · ${t("vehicles.split_at", { at: when(m.split_at) })}` : ""}
            </span>
            {m.state === "merged" ? (
              splitting === m.id ? (
                <SplitForm vehicle={vehicle} merge={m} onDone={() => setSplitting(null)} />
              ) : (
                <SplitButton onClick={() => setSplitting(m.id)} />
              )
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SplitButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  const can = useCan();
  if (!can("vehicle.split")) return null;
  return (
    <div className="actions">
      <button type="button" className="secondary" onClick={onClick}>
        {t("vehicles.split_open")}
      </button>
    </div>
  );
}

function SplitForm({ vehicle, merge, onDone }: { vehicle: Vehicle; merge: Merge; onDone: () => void }) {
  const { t } = useTranslation();
  const company = useCompany();
  const when = useWhen();
  const refresh = useRefreshVehicle(vehicle.id);
  const path = `/c/${company.public_id}/vehicles/${vehicle.id}/merges/${merge.id}/split`;
  const preview = useApi<{ records: SplitRecord[] }>(["vehicle-split", company.public_id, merge.id], `${path}/preview`);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const split = useCommand<Record<string, unknown>, unknown>(path);
  const top = (preview.data?.records ?? []).filter((r) => !r.follows);
  const complete = top.every((r) => assignments[r.key]);
  return (
    <div className="stack">
      {preview.isLoading ? <Loading /> : null}
      {top.length === 0 && preview.data ? <p className="muted small">{t("vehicles.split_no_records")}</p> : null}
      {top.length ? (
        <>
          <p className="small">{t("vehicles.split_records_hint")}</p>
          <table>
            <tbody>
              {top.map((r) => (
                <tr key={r.key}>
                  <td>
                    {t(`vehicles.split_kind_${r.kind}`)} {r.label} · {when(r.at)}
                  </td>
                  {(["primary", "duplicate"] as const).map((side) => (
                    <td key={side}>
                      <label>
                        <input
                          type="radio"
                          name={`split-${r.key}`}
                          checked={assignments[r.key] === side}
                          onChange={() => setAssignments({ ...assignments, [r.key]: side })}
                        />{" "}
                        {side === "primary" ? merge.primary.name : merge.duplicate.name}
                      </label>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
      <Field label={t("vehicles.split_reason")}>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <ErrorMessage error={split.error} />
      <div className="actions">
        <button
          type="button"
          className="danger"
          disabled={!complete || !reason.trim() || split.pending}
          onClick={async () => {
            if ((await split.run({ assignments, reason })) !== null) {
              await refresh();
              onDone();
            }
          }}
        >
          {t("vehicles.split_submit")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

const NUMBER_FIELDS = new Set(["year", "power_hp", "seats", "doors"]);
const CHOICES: Record<string, [keyof VehicleOptions, string]> = {
  vehicle_type: ["vehicle_types", "vehicles.type_"],
  body_type: ["body_types", "vehicles.body."],
  fuel_type: ["fuel_types", "vehicles.fuel."],
  transmission: ["transmissions", "vehicles.gearbox."],
  drive: ["drives", "vehicles.drives."],
};

/** § 8.2.1: confirmed values and the proposal of a correction with its basis; until the decision
 * the company may use its own working value, marked as a discrepancy. */
export function ProposalsCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const when = useWhen();
  const refresh = useRefreshVehicle(vehicle.id);
  const options = useApi<VehicleOptions>(["vehicle-options"], "/catalog/vehicle-options");
  const confirmed = vehicle.confirmed_fields ?? [];
  const proposals = vehicle.proposals ?? [];
  const [field, setField] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [working, setWorking] = useState(true);
  const propose = useCommand<Record<string, unknown>, unknown>(`/c/${company.public_id}/vehicles/${vehicle.id}/proposals`);
  if (!confirmed.length && !proposals.length) return null;
  const allowed =
    can("vehicle.edit") && vehicle.state === "active" && (vehicle.relation === "own" || vehicle.relation === "managed");
  const choice = CHOICES[field];
  const list = choice && options.data ? (options.data[choice[0]] as string[]) : null;
  return (
    <Card title={t("vehicles.proposals_title")}>
      <p className="muted small">{t("vehicles.proposals_hint")}</p>
      {proposals.length ? (
        <ul className="plain">
          {proposals.map((p) => (
            <li key={p.id} className="small">
              {t(`vehicles.${p.field}`)}: {p.current.display || "—"} → <strong>{p.proposed.display || "—"}</strong> ·{" "}
              {t(`vehicles.proposal_state_${p.state}`)} · {when(p.decided_at ?? p.created_at)}
              {p.decision_reason ? ` · ${p.decision_reason}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {allowed && confirmed.length ? (
        <div className="stack">
          <div className="row">
            <Field label={t("vehicles.proposal_field")}>
              <select
                value={field}
                onChange={(e) => {
                  setField(e.target.value);
                  setValue("");
                }}
              >
                <option value="">—</option>
                {confirmed.map((f) => (
                  <option key={f} value={f}>
                    {t(`vehicles.${f}`)}
                  </option>
                ))}
              </select>
            </Field>
            {field ? (
              <Field label={t("vehicles.proposal_value")}>
                {list && choice ? (
                  <select value={value} onChange={(e) => setValue(e.target.value)}>
                    <option value="">—</option>
                    {list.map((code) => (
                      <option key={code} value={code}>
                        {t(`${choice[1]}${code}`)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={value}
                    inputMode={NUMBER_FIELDS.has(field) ? "numeric" : undefined}
                    onChange={(e) => setValue(e.target.value)}
                  />
                )}
              </Field>
            ) : null}
          </div>
          <Field label={t("vehicles.proposal_reason")}>
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <label className="small">
            <input type="checkbox" checked={working} onChange={(e) => setWorking(e.target.checked)} />{" "}
            {t("vehicles.proposal_use_working")}
          </label>
          <ErrorMessage error={propose.error} />
          <div className="actions">
            <button
              type="button"
              disabled={!field || !reason.trim() || propose.pending}
              onClick={async () => {
                const body: Record<string, unknown> = { field, reason, use_working: working };
                if (field === "make" || field === "model") body[`${field}_other`] = value;
                else body[field] = NUMBER_FIELDS.has(field) ? (value === "" ? null : Number(value)) : value;
                if ((await propose.run(body)) !== null) {
                  setField("");
                  setValue("");
                  setReason("");
                  await refresh();
                }
              }}
            >
              {t("vehicles.proposal_submit")}
            </button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

/** § 8.6.6: the records of the duplicates merged into this card keep their origin and are shown
 * from it. */
export function MergedHistory({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const when = useWhen();
  const items = vehicle.merged_cards ?? [];
  const [open, setOpen] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <Card title={t("vehicles.merged_cards_title")}>
      <p className="muted small">{t("vehicles.merged_cards_hint")}</p>
      {items.map((m) => (
        <div key={m.id} className="stack">
          <div className="row">
            <span>
              «{m.name}» · {m.vehicle_id} · {t("vehicles.merged_at", { at: when(m.merged_at) })}
            </span>
            <button type="button" className="secondary" onClick={() => setOpen(open === m.id ? null : m.id)}>
              {open === m.id ? t("vehicles.merged_hide") : t("vehicles.merged_show")}
            </button>
          </div>
          {open === m.id ? <MergedCardHistory id={m.id} /> : null}
        </div>
      ))}
    </Card>
  );
}

function MergedCardHistory({ id }: { id: string }) {
  const company = useCompany();
  const found = useApi<{ vehicle: Vehicle }>(["vehicle", company.public_id, id], `/c/${company.public_id}/vehicles/${id}`);
  if (!found.data) return <Loading />;
  const merged = found.data.vehicle;
  return (
    <div className="stack">
      <VehicleService vehicle={merged} />
      <VehicleInspectionsCard vehicle={merged} />
      <VehicleDamagesCard vehicle={merged} />
      <LinkedDocuments objectType="vehicle" objectId={merged.id} readOnly />
    </div>
  );
}
