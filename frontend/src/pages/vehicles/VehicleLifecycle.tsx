import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "../../api/queries";
import type { Vehicle, VehicleRestriction } from "../../api/types";
import { useCommand } from "../../api/useCommand";
import { Card, ErrorMessage, Field } from "../../components/ui";
import { useCan, useCompany } from "../../layout/CompanyShell";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useLocations } from "../settings/LocationsPage";

/** Block 12, part Б: the lifecycle of the vehicle card (ТЗ § 8.1.6, § 8.2.3–8.2.13, § 8.3, § 8.4.2,
 * § 8.7.5–8.7.6, § 8.8). */

function useWhen() {
  const { i18n } = useTranslation();
  const company = useCompany();
  return (value: string | null | undefined) => formatMoment(value ?? null, company.timezone ?? "UTC", i18n.language);
}

function useRefresh(id: string) {
  const company = useCompany();
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["vehicle", company.public_id, id] });
    await client.invalidateQueries({ queryKey: ["vehicles", company.public_id] });
  };
}

function useVehicleCommand(vehicle: Vehicle, action: string, method: "POST" | "PUT" = "POST") {
  const company = useCompany();
  return useCommand<Record<string, unknown>, { vehicle: Vehicle }>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/${action}`,
    method,
  );
}

type Reasons = { system: string[]; company: { id: string; name: string }[]; contexts: string[] };

function reasonText(t: (key: string) => string, r: VehicleRestriction) {
  return r.custom_reason ? r.custom_reason.name : t(`vehicles.restriction_reason_${r.reason}`);
}

/** § 8.8: one manual restriction at a time with its reason, term and forbidden actions. Setting,
 * changing and lifting are three separate rights (§ 23.5.5). */
export function RestrictionCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const when = useWhen();
  const current = vehicle.restriction ?? null;
  const [editing, setEditing] = useState(false);
  const lift = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/restrictions/${current?.id ?? ""}/lift`,
  );
  const refresh = useRefresh(vehicle.id);
  const [liftComment, setLiftComment] = useState("");
  if (vehicle.state !== "active" && !current) return null;
  const forbids = (r: VehicleRestriction) =>
    [
      r.forbid_rental_issue ? t("vehicles.restriction_forbid_rental_issue") : "",
      r.forbid_moves ? t("vehicles.restriction_forbid_moves") : "",
      ...r.forbidden_contexts.map((c) => t("vehicles.restriction_forbid_context", { context: t(`contexts.${c}`) })),
    ]
      .filter(Boolean)
      .join("; ");
  return (
    <Card title={t("vehicles.restriction_title")}>
      {current ? (
        <div className={current.state === "active" ? "alert warn" : "alert"}>
          <strong>{reasonText(t, current)}</strong>
          {current.comment ? ` — ${current.comment}` : ""}
          <div className="small">
            {t(`vehicles.restriction_state_${current.state}`)} · {when(current.starts_at)} —{" "}
            {current.ends_at ? when(current.ends_at) : t("vehicles.restriction_no_end")}
          </div>
          <div className="small">{forbids(current)}</div>
        </div>
      ) : (
        <p className="muted">{t("vehicles.restriction_none")}</p>
      )}
      <p className="muted small">{t("vehicles.restriction_hint")}</p>
      {editing ? (
        <RestrictionForm vehicle={vehicle} current={current} onDone={() => setEditing(false)} />
      ) : (
        <div className="actions">
          {!current && can("vehicle.restriction.set") ? (
            <button type="button" onClick={() => setEditing(true)}>
              {t("vehicles.restriction_set")}
            </button>
          ) : null}
          {current && can("vehicle.restriction.change") ? (
            <button type="button" className="secondary" onClick={() => setEditing(true)}>
              {t("vehicles.restriction_change")}
            </button>
          ) : null}
        </div>
      )}
      {current && can("vehicle.restriction.lift") && !editing ? (
        <div className="row">
          <Field label={t("vehicles.restriction_lift_comment")}>
            <input value={liftComment} onChange={(e) => setLiftComment(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={lift.pending}
              onClick={async () => {
                if ((await lift.run({ expected_version: current.version, comment: liftComment })) !== null) {
                  setLiftComment("");
                  await refresh();
                }
              }}
            >
              {t("vehicles.restriction_lift")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={lift.error} />
    </Card>
  );
}

function RestrictionForm({
  vehicle,
  current,
  onDone,
}: {
  vehicle: Vehicle;
  current: VehicleRestriction | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const refresh = useRefresh(vehicle.id);
  const zone = company.timezone ?? "UTC";
  const reasons = useApi<Reasons>(["restriction-reasons", company.public_id], `/c/${company.public_id}/vehicle-restriction-reasons`);
  const initialReason = current ? (current.custom_reason ? `custom:${current.custom_reason.id}` : current.reason) : "technical_fault";
  const [reason, setReason] = useState(initialReason);
  const [comment, setComment] = useState(current?.comment ?? "");
  const [startsAt, setStartsAt] = useState(current ? toLocalInput(new Date(current.starts_at), zone) : "");
  const [endsAt, setEndsAt] = useState(current?.ends_at ? toLocalInput(new Date(current.ends_at), zone) : "");
  const [rental, setRental] = useState(current?.forbid_rental_issue ?? true);
  const [moves, setMoves] = useState(current?.forbid_moves ?? false);
  const [contexts, setContexts] = useState<string[]>(current?.forbidden_contexts ?? []);
  const [newReason, setNewReason] = useState("");
  const create = useVehicleCommand(vehicle, "restrictions");
  const change = useCommand<Record<string, unknown>, unknown>(
    `/c/${company.public_id}/vehicles/${vehicle.id}/restrictions/${current?.id ?? ""}`,
    "PUT",
  );
  const addReason = useCommand<{ name: string }, Reasons>(`/c/${company.public_id}/vehicle-restriction-reasons`);
  const client = useQueryClient();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const custom = reason.startsWith("custom:");
    const body: Record<string, unknown> = {
      reason: custom ? "custom" : reason,
      custom_reason_id: custom ? reason.slice(7) : null,
      comment,
      starts_at: startsAt ? fromLocalInput(startsAt, zone) : null,
      ends_at: endsAt ? fromLocalInput(endsAt, zone) : null,
      forbid_rental_issue: rental,
      forbid_moves: moves,
      forbidden_contexts: contexts,
    };
    const result = current ? await change.run({ ...body, expected_version: current.version }) : await create.run(body);
    if (result !== null) {
      await refresh();
      onDone();
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="row">
        <Field label={t("vehicles.restriction_reason")}>
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            {(reasons.data?.system ?? []).map((r) => (
              <option key={r} value={r}>
                {t(`vehicles.restriction_reason_${r}`)}
              </option>
            ))}
            {(reasons.data?.company ?? []).map((r) => (
              <option key={r.id} value={`custom:${r.id}`}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("vehicles.restriction_comment")}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        <Field label={t("vehicles.restriction_starts_at")} hint={t("vehicles.restriction_starts_hint")}>
          <input type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} />
        </Field>
        <Field label={t("vehicles.restriction_ends_at")} hint={t("vehicles.restriction_ends_hint")}>
          <input type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} />
        </Field>
      </div>
      <fieldset>
        <legend>{t("vehicles.restriction_forbidden")}</legend>
        <label style={{ marginRight: 16 }}>
          <input type="checkbox" checked={rental} onChange={(e) => setRental(e.target.checked)} />{" "}
          {t("vehicles.restriction_forbid_rental_issue")}
        </label>
        <label style={{ marginRight: 16 }}>
          <input type="checkbox" checked={moves} onChange={(e) => setMoves(e.target.checked)} />{" "}
          {t("vehicles.restriction_forbid_moves")}
        </label>
        {(reasons.data?.contexts ?? []).map((c) => (
          <label key={c} style={{ marginRight: 16 }}>
            <input
              type="checkbox"
              checked={contexts.includes(c)}
              onChange={(e) => setContexts(e.target.checked ? [...contexts, c] : contexts.filter((x) => x !== c))}
            />{" "}
            {t("vehicles.restriction_forbid_context", { context: t(`contexts.${c}`) })}
          </label>
        ))}
      </fieldset>
      {can("vehicle.restriction.set") ? (
        <div className="row">
          <Field label={t("vehicles.restriction_new_reason")}>
            <input value={newReason} onChange={(e) => setNewReason(e.target.value)} />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={!newReason.trim() || addReason.pending}
              onClick={async () => {
                if ((await addReason.run({ name: newReason })) !== null) {
                  setNewReason("");
                  await client.invalidateQueries({ queryKey: ["restriction-reasons", company.public_id] });
                }
              }}
            >
              {t("vehicles.restriction_add_reason")}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorMessage error={create.error ?? change.error ?? addReason.error} />
      <div className="actions">
        <button type="submit" disabled={create.pending || change.pending}>
          {t("common.save")}
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

type UnitPreview = {
  from_unit: string;
  to_unit: string;
  method: string;
  factor: string;
  changes: Record<string, { what: string; field: string; before: unknown; after: unknown }[]>;
};

/** § 8.2.10, § 8.2.13: the accumulated mileage, the replacements of the odometer and the change of
 * its unit with an explicit method, shown before it is confirmed. */
export function OdometerCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const when = useWhen();
  const refresh = useRefresh(vehicle.id);
  const target = vehicle.odometer_unit === "km" ? "mi" : "km";
  const [method, setMethod] = useState("");
  const [reason, setReason] = useState("");
  const change = useVehicleCommand(vehicle, "odometer-unit");
  const preview = useApi<{ preview: UnitPreview }>(
    ["odometer-unit", company.public_id, vehicle.id, method, vehicle.version],
    `/c/${company.public_id}/vehicles/${vehicle.id}/odometer-unit/preview?to_unit=${target}&method=${method}`,
    Boolean(method) && can("vehicle.odometer_unit"),
  );
  const unit = (u: string) => t(`vehicles.unit_${u}`);
  const replacements = vehicle.odometer_replacements ?? [];
  const history = vehicle.odometer_unit_history ?? [];
  const sample = preview.data?.preview.changes.vehicle?.slice(0, 3) ?? [];
  const count = preview.data ? Object.values(preview.data.preview.changes).reduce((n, list) => n + list.length, 0) : 0;
  return (
    <Card title={t("vehicles.odometer_title")}>
      <p>
        {t("vehicles.odometer")}:{" "}
        {vehicle.odometer ? `${Number(vehicle.odometer.value)} ${unit(vehicle.odometer.unit)}` : "—"}
        {vehicle.odometer?.accumulated && vehicle.odometer.accumulated !== vehicle.odometer.value ? (
          <> · {t("vehicles.odometer_accumulated", { value: Number(vehicle.odometer.accumulated), unit: unit(vehicle.odometer.unit) })}</>
        ) : null}
      </p>
      {replacements.length ? (
        <>
          <h3>{t("vehicles.replacements_title")}</h3>
          <ul className="plain">
            {replacements.map((r) => (
              <li key={r.id} className="small">
                {when(r.replaced_at)} · {t("vehicles.replacement_line", {
                  old: Number(r.old_final),
                  start: Number(r.new_start),
                  accumulated: Number(r.accumulated),
                  unit: unit(r.unit),
                })}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="muted small">{t("vehicles.replacement_where")}</p>
      {can("vehicle.odometer_unit") && vehicle.state === "active" ? (
        <>
          <h3>{t("vehicles.unit_change_title", { from: unit(vehicle.odometer_unit), to: unit(target) })}</h3>
          <div className="row">
            <Field label={t("vehicles.unit_change_method")}>
              <select value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="">—</option>
                <option value="relabel">{t("vehicles.unit_method_relabel")}</option>
                <option value="convert">{t("vehicles.unit_method_convert")}</option>
              </select>
            </Field>
            <Field label={t("vehicles.unit_change_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          {method ? <p className="muted small">{t(`vehicles.unit_method_${method}_hint`)}</p> : null}
          {preview.data ? (
            <div className="small">
              <p>{t("vehicles.unit_change_preview", { count })}</p>
              <ul className="plain">
                {sample.map((c, i) => (
                  <li key={i}>
                    {String(c.before)} → {String(c.after)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <ErrorMessage error={change.error ?? (preview.error as never)} />
          <div className="actions">
            <button
              type="button"
              disabled={!method || change.pending || !preview.data}
              onClick={async () => {
                if ((await change.run({ to_unit: target, method, reason, expected_version: vehicle.version })) !== null) {
                  setMethod("");
                  setReason("");
                  await refresh();
                }
              }}
            >
              {t("vehicles.unit_change_button")}
            </button>
          </div>
        </>
      ) : null}
      {history.length ? (
        <ul className="plain small">
          {history.map((h) => (
            <li key={h.id}>
              {when(h.changed_at)} · {unit(h.from_unit)} → {unit(h.to_unit)} · {t(`vehicles.unit_method_${h.method}`)}
              {h.reason ? ` · ${h.reason}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

/** § 8.1.6, § 8.3.17, § 8.7.5–8.7.6: the relation, the end of the management, the archive and
 * the restore of the earlier card. */
export function RelationCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const can = useCan();
  const refresh = useRefresh(vehicle.id);
  const locations = useLocations();
  const [reason, setReason] = useState("");
  const [then, setThen] = useState("archive");
  const [location, setLocation] = useState(vehicle.location_id ?? "");
  const [relation, setRelation] = useState("managed");
  const archive = useVehicleCommand(vehicle, "archive");
  const end = useVehicleCommand(vehicle, "end-management");
  const take = useVehicleCommand(vehicle, "take-into-management");
  const restore = useVehicleCommand(vehicle, "restore");
  const active = (locations.data?.locations ?? []).filter((l) => l.state !== "deactivated");
  const archived = vehicle.state === "archived";
  const run = async (command: ReturnType<typeof useVehicleCommand>, body: Record<string, unknown>) => {
    if ((await command.run({ ...body, reason, expected_version: vehicle.version })) !== null) {
      setReason("");
      await refresh();
    }
  };
  const mayArchive = can("vehicle.archive");
  const mayRelation = can("vehicle.relation");
  if (vehicle.state === "merged" || (!mayArchive && !mayRelation)) return null;
  if (!archived && vehicle.relation === "own") return null;
  const locationField = (
    <Field label={t("vehicles.location")}>
      <select value={location} onChange={(e) => setLocation(e.target.value)}>
        <option value="">—</option>
        {active.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <Card title={archived ? t("vehicles.restore_title") : t("vehicles.relation_title")}>
      {archived ? (
        <>
          <p className="muted small">{t("vehicles.restore_hint")}</p>
          <div className="row">
            <Field label={t("vehicles.relation")}>
              <select value={relation} onChange={(e) => setRelation(e.target.value)}>
                {["own", "managed", "client"].map((r) => (
                  <option key={r} value={r}>
                    {t(`vehicles.relation_${r}`)}
                  </option>
                ))}
              </select>
            </Field>
            {relation === "client" ? null : locationField}
            <Field label={t("vehicles.lifecycle_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          <ErrorMessage error={restore.error} />
          <div className="actions">
            <button
              type="button"
              disabled={!mayArchive || !reason.trim() || (relation !== "client" && !location) || restore.pending}
              onClick={() =>
                void run(restore, {
                  relation,
                  location_id: relation === "client" ? null : location,
                  owner_kind: relation === "client" ? "none" : relation === "managed" ? "none" : undefined,
                })
              }
            >
              {t("vehicles.restore_button")}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="row">
            <Field label={t("vehicles.lifecycle_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            {vehicle.relation === "managed" ? (
              <Field label={t("vehicles.end_management_then")}>
                <select value={then} onChange={(e) => setThen(e.target.value)}>
                  <option value="archive">{t("vehicles.end_management_archive")}</option>
                  <option value="client">{t("vehicles.end_management_client")}</option>
                </select>
              </Field>
            ) : null}
            {vehicle.relation === "client" ? locationField : null}
          </div>
          <p className="muted small">
            {vehicle.relation === "managed" ? t("vehicles.end_management_hint") : t("vehicles.archive_hint")}
          </p>
          <ErrorMessage error={archive.error ?? end.error ?? take.error} />
          <div className="actions">
            {vehicle.relation === "managed" && mayRelation ? (
              <button type="button" disabled={!reason.trim() || end.pending} onClick={() => void run(end, { then })}>
                {t("vehicles.end_management_button")}
              </button>
            ) : null}
            {vehicle.relation === "client" && mayRelation ? (
              <button
                type="button"
                className="secondary"
                disabled={!reason.trim() || !location || take.pending}
                onClick={() => void run(take, { location_id: location, owner_kind: "none" })}
              >
                {t("vehicles.take_into_management_button")}
              </button>
            ) : null}
            {vehicle.relation === "client" && mayArchive ? (
              <button type="button" className="secondary" disabled={!reason.trim() || archive.pending} onClick={() => void run(archive, {})}>
                {t("vehicles.archive_button")}
              </button>
            ) : null}
          </div>
        </>
      )}
    </Card>
  );
}

/** § 8.4.2: a write-off or a disposal of an own vehicle only from the warehouse, with its right. */
export function DisposeCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const can = useCan();
  const refresh = useRefresh(vehicle.id);
  const [kind, setKind] = useState("write_off");
  const [reason, setReason] = useState("");
  const dispose = useVehicleCommand(vehicle, "dispose");
  if (!can("vehicle.dispose") || vehicle.storage === null || vehicle.state !== "active") return null;
  return (
    <Card title={t("vehicles.dispose_title")}>
      <p className="muted small">{t("vehicles.dispose_hint")}</p>
      <div className="row">
        <Field label={t("vehicles.dispose_kind")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="write_off">{t("vehicles.dispose_write_off")}</option>
            <option value="disposal">{t("vehicles.dispose_disposal")}</option>
          </select>
        </Field>
        <Field label={t("vehicles.lifecycle_reason")}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
      <ErrorMessage error={dispose.error} />
      <div className="actions">
        <button
          type="button"
          disabled={!reason.trim() || dispose.pending}
          onClick={async () => {
            if ((await dispose.run({ kind, reason, expected_version: vehicle.version })) !== null) await refresh();
          }}
        >
          {t("vehicles.dispose_button")}
        </button>
      </div>
    </Card>
  );
}

/** § 8.3.9: after a sale to a person the seller asks the new owner to confirm a wrong transfer. */
export function ReversalCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const can = useCan();
  const when = useWhen();
  const refresh = useRefresh(vehicle.id);
  const [reason, setReason] = useState("");
  const ask = useVehicleCommand(vehicle, "transfer-reversal");
  const items = vehicle.transfer_reversals ?? [];
  if (!vehicle.sold_to_profile && !items.length) return null;
  const open = items.some((r) => r.state === "open");
  return (
    <Card title={t("vehicles.reversal_title")}>
      <p className="muted small">{t("vehicles.reversal_hint")}</p>
      <ul className="plain small">
        {items.map((r) => (
          <li key={r.id}>
            {when(r.created_at)} · {r.reason} · <span className="badge">{t(`vehicles.reversal_state_${r.state}`)}</span>
            {r.answer_comment ? ` · ${r.answer_comment}` : ""}
          </li>
        ))}
      </ul>
      {vehicle.sold_to_profile && !open && can("vehicle.ownership_reversal") ? (
        <>
          <div className="row">
            <Field label={t("vehicles.lifecycle_reason")}>
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
          <ErrorMessage error={ask.error} />
          <div className="actions">
            <button
              type="button"
              disabled={!reason.trim() || ask.pending}
              onClick={async () => {
                if ((await ask.run({ reason })) !== null) {
                  setReason("");
                  await refresh();
                }
              }}
            >
              {t("vehicles.reversal_button")}
            </button>
          </div>
        </>
      ) : null}
    </Card>
  );
}

/** § 8.3.7, § 8.3.15: the categories passed to the owner of a managed vehicle. */
export function DisclosureCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const can = useCan();
  const when = useWhen();
  const refresh = useRefresh(vehicle.id);
  const data = vehicle.disclosure;
  const [chosen, setChosen] = useState<string[] | null>(null);
  const save = useVehicleCommand(vehicle, "disclosure", "PUT");
  if (!data) return null;
  const current = chosen ?? data.categories;
  const editable = can("vehicle.disclosure") && vehicle.state === "active";
  return (
    <Card title={t("vehicles.disclosure_title")}>
      <p className="muted small">{t("vehicles.disclosure_hint")}</p>
      <p className="small">
        {data.confirmed_since
          ? t("vehicles.disclosure_confirmed", { at: when(data.confirmed_since) })
          : t("vehicles.disclosure_not_confirmed")}
      </p>
      {data.all.map((c) => (
        <label key={c} style={{ display: "block" }}>
          <input
            type="checkbox"
            disabled={!editable}
            checked={current.includes(c)}
            onChange={(e) => setChosen(e.target.checked ? [...current, c] : current.filter((x) => x !== c))}
          />{" "}
          {t(`vehicles.disclosure_${c}`)}
        </label>
      ))}
      <ErrorMessage error={save.error} />
      {editable ? (
        <div className="actions">
          <button
            type="button"
            disabled={chosen === null || save.pending}
            onClick={async () => {
              if ((await save.run({ categories: current })) !== null) {
                setChosen(null);
                await refresh();
              }
            }}
          >
            {t("common.save")}
          </button>
        </div>
      ) : null}
      {data.history.length > 1 ? (
        <ul className="plain small">
          {data.history.map((h, i) => (
            <li key={i}>
              {when(h.effective_from)} · {h.categories.map((c) => t(`vehicles.disclosure_${c}`)).join(", ") || "—"}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

/** § 8.2.3, § 8.2.7: the global documents the company may read and its own copies, marked as
 * given by the company and not confirmed by the owner. */
export function GlobalDocumentsCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const company = useCompany();
  const data = vehicle.global_documents;
  if (!data) return null;
  return (
    <Card title={t("vehicles.global_documents_title")}>
      <p className="muted small">
        {data.access ? t(`vehicles.global_documents_access_${data.access}`) : t("vehicles.global_documents_no_access")}
      </p>
      {data.documents.map((d) => (
        <div key={d.id} style={{ marginBottom: 8 }}>
          <strong>{t(`vehicles.document_type_${d.doc_type}`)}</strong>
          {d.title ? ` · ${d.title}` : ""}
          <ul className="plain small">
            {d.versions.map((v) => (
              <li key={v.id}>
                v{v.number} · {v.details || "—"}
                {v.valid_until ? ` · ${t("vehicles.document_valid_until", { date: v.valid_until })}` : ""}
                {v.source === "company_copy" ? ` · ${t("vehicles.document_from_copy")}` : ""}
                {v.has_file ? (
                  <>
                    {" "}
                    ·{" "}
                    <a href={`/api/c/${company.public_id}/vehicles/${vehicle.id}/global-documents/versions/${v.id}/file`} target="_blank" rel="noreferrer">
                      {t("vehicles.document_file")}
                    </a>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {data.copies.length ? (
        <>
          <h3>{t("vehicles.company_copies_title")}</h3>
          <ul className="plain small">
            {data.copies.map((c) => (
              <li key={c.version_id}>
                {c.title} v{c.number} ·{" "}
                <span className="badge warn">
                  {c.owner_decision ? t(`vehicles.copy_decision_${c.owner_decision}`) : t("vehicles.copy_unconfirmed")}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Card>
  );
}

/** § 8.2.6, § 8.2.11, § 8.7.6: periods of the company's work, owners and plates. */
export function HistoryCard({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useTranslation();
  const when = useWhen();
  const periods = vehicle.periods ?? [];
  const owners = vehicle.owners_history ?? [];
  const plates = vehicle.plates_history ?? [];
  return (
    <Card title={t("vehicles.history_title")}>
      {vehicle.archive ? (
        <div className="alert">{t("vehicles.archived_banner", { at: when(vehicle.archive.at), reason: vehicle.archive.reason })}</div>
      ) : null}
      {vehicle.disposal ? (
        <div className="alert">{t(`vehicles.dispose_${vehicle.disposal.kind}`)} · {vehicle.disposal.reason}</div>
      ) : null}
      <h3>{t("vehicles.periods_title")}</h3>
      <ul className="plain small">
        {periods.map((p, i) => (
          <li key={i}>
            {t(`vehicles.relation_${p.relation}`)} · {when(p.started_at)} — {p.ended_at ? when(p.ended_at) : t("vehicles.period_now")} ·{" "}
            {t(`vehicles.period_basis_${p.start_basis}`)}
            {p.end_basis ? ` → ${t(`vehicles.period_basis_${p.end_basis}`)}` : ""}
            {p.start_reason ? ` · ${p.start_reason}` : ""}
          </li>
        ))}
      </ul>
      <h3>{t("vehicles.owners_title")}</h3>
      {owners.length === 0 ? <p className="muted small">{t("vehicles.owners_none")}</p> : null}
      <ul className="plain small">
        {owners.map((o, i) => (
          <li key={i}>
            {o.name || t(`vehicles.history_owner_kind_${o.owner}`)} · {o.started_approx ? "~" : ""}
            {when(o.started_at)} — {o.ended_at ? `${o.ended_approx ? "~" : ""}${when(o.ended_at)}` : t("vehicles.period_now")}
            {o.reversed ? ` · ${t("vehicles.owner_reversed")}` : ""}
          </li>
        ))}
      </ul>
      <h3>{t("vehicles.plates_title")}</h3>
      {plates.length === 0 ? <p className="muted small">—</p> : null}
      <ul className="plain small">
        {plates.map((p, i) => (
          <li key={i}>
            {p.number} {p.country} {p.region} · {when(p.valid_from)} — {p.valid_to ? when(p.valid_to) : t("vehicles.period_now")}
          </li>
        ))}
      </ul>
    </Card>
  );
}
