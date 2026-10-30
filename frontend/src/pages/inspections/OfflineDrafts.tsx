/**
 * Local drafts of inspections (ТЗ § 10.4.2–10.4.4): the list with the sync state of each, the
 * amount of local data and its warnings; a new inspection started without connection — kind,
 * vehicle from the list loaded earlier or its internal ID or plate, actual moment, odometer,
 * fuel and photos — and its sending once the connection is back: the employee confirms the
 * sign-in, links the draft with the vehicle and the operation and sends it; possible
 * duplicates are resolved by the employee.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../../api/client";
import type { InspectionApplication, InspectionKind } from "../../api/types";
import { useMe } from "../../auth";
import { Card, ErrorMessage, Field, Loading } from "../../components/ui";
import { companyPath, useCan, useCompany } from "../../layout/CompanyShell";
import {
  addFile,
  deleteDraft,
  expiresAt,
  expiry,
  getBlob,
  makeNewDraft,
  removeFile,
  saveDraft,
  storageEstimate,
  transition,
  updateDraft,
  usage,
  type Draft,
  type DraftFile,
  type NewDraft,
  type NewDraftData,
  type Scope,
  type SyncState,
  type Usage,
} from "../../offline/drafts";
import {
  useDataset,
  useDraft,
  useDrafts,
  useOnline,
  useScope,
  type OfflineTemplate,
  type OfflineVehicle,
} from "../../offline/hooks";
import { isSessionLost, syncDraft, type SyncResult } from "../../offline/sync";
import { formatMoment, fromLocalInput, toLocalInput } from "../../time";
import { useServiceOperations } from "../service/hooks";
import { useRentals, useVehicles } from "../vehicles/VehiclesPage";
import { APPLICATIONS, KINDS, KINDS_OF, stateBadge, usePositionName, useRefreshInspections } from "./data";
import { PhotoInput, VideoInput } from "./Media";

const MOTORCYCLE_TYPES = ["motorcycle", "moped"];

function syncBadge(state: SyncState): string {
  if (state === "synced") return "ok";
  if (state === "conflict" || state === "error") return "danger";
  if (state === "waiting" || state === "uploading") return "warn";
  return "";
}

export function SyncBadge({ state }: { state: SyncState }) {
  const { t } = useTranslation();
  return <span className={`badge ${syncBadge(state)}`}>{t(`offline.state_${state}`)}</span>;
}

export function useBytes(): (bytes: number) => string {
  const { t, i18n } = useTranslation();
  return (bytes) => {
    const format = (value: number) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(value);
    if (bytes < 1024 * 1024) return t("offline.size_kb", { value: format(bytes / 1024) });
    if (bytes < 1024 * 1024 * 1024) return t("offline.size_mb", { value: format(bytes / 1024 / 1024) });
    return t("offline.size_gb", { value: format(bytes / 1024 / 1024 / 1024) });
  };
}

/** A photo kept on the device, shown from its Blob. */
export function LocalThumb({
  scope,
  file,
  onRemove,
  size = 96,
}: {
  scope: Scope;
  file: Pick<DraftFile, "id" | "kind">;
  onRemove?: () => void;
  size?: number;
}) {
  const { t } = useTranslation();
  const url = useBlobUrl(scope, file.id);
  return (
    <span className="media-thumb" style={{ width: size, height: size }}>
      {url ? file.kind === "photo" ? <img src={url} alt="" /> : <video src={url} muted playsInline /> : null}
      <span className="badge warn media-local">{t("offline.on_device")}</span>
      {onRemove ? (
        <button type="button" className="media-remove" aria-label={t("inspections.remove_file")} onClick={onRemove}>
          ×
        </button>
      ) : null}
    </span>
  );
}

function useBlobUrl(scope: Scope, fileId: string): string | null {
  const [url, setUrl] = useState<{ id: string; url: string | null }>({ id: "", url: null });
  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    void getBlob(scope, fileId).then((blob) => {
      if (!alive || !blob) return;
      created = URL.createObjectURL(blob);
      setUrl({ id: fileId, url: created });
    });
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, scope.userId, scope.companyId]);
  return url.id === fileId ? url.url : null;
}

/** The amount of local data and what the employee has to know about it (§ 10.4.4). */
export function StorageNotice({ scope }: { scope: Scope }) {
  const { t } = useTranslation();
  const bytes = useBytes();
  const { drafts } = useDrafts(scope);
  const [own, setOwn] = useState<Usage | null>(null);
  const [site, setSite] = useState<{ usage: number; quota: number } | null>(null);
  useEffect(() => {
    let alive = true;
    void usage(scope).then((value) => alive && setOwn(value));
    void storageEstimate().then((value) => alive && setSite(value));
    return () => {
      alive = false;
    };
  }, [scope, drafts]);
  return (
    <div className="stack">
      <p className="muted small">
        {t("offline.usage", { drafts: own?.drafts ?? 0, files: own?.files ?? 0, size: bytes(own?.bytes ?? 0) })}
        {site ? ` ${t("offline.usage_site", { size: bytes(site.usage), quota: bytes(site.quota) })}` : ""}
      </p>
      <p className="alert warn">{t("offline.clear_warning")}</p>
      <p className="muted small">{t("offline.gallery_note")}</p>
    </div>
  );
}

function vehicleLabel(draft: NewDraft, vehicles: OfflineVehicle[] | undefined): string {
  const vehicle = vehicles?.find((v) => v.id === draft.data.vehicleCardId);
  if (vehicle) return `${vehicle.name} · ${vehicle.vehicle_id}${vehicle.plate ? ` · ${vehicle.plate}` : ""}`;
  return draft.data.vehicleText || "—";
}

/** Local drafts of the account in the company (§ 10.4.4); «base» is the company's path. */
export function DraftsPanel({
  scope,
  base,
  zone,
  canCreate,
  always,
}: {
  scope: Scope;
  base: string;
  zone: string;
  canCreate: boolean;
  /** Shown even without drafts (the offline start page). */
  always?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { drafts, error } = useDrafts(scope);
  const vehicles = useDataset<OfflineVehicle>(scope, "vehicles");
  const [creating, setCreating] = useState(false);
  const [failed, setFailed] = useState(false);

  async function start() {
    setCreating(true);
    setFailed(false);
    try {
      const draft = await saveDraft(makeNewDraft(scope));
      navigate(`${base}/inspections/drafts/${draft.id}`);
    } catch {
      setFailed(true);
    } finally {
      setCreating(false);
    }
  }

  if (drafts === null) return always ? <Loading /> : null;
  if (!always && drafts.length === 0) return null;
  return (
    <Card title={t("offline.drafts_title")}>
      <div className="stack">
        <p className="muted small">{t("offline.drafts_hint")}</p>
        {error || failed ? <p className="alert error">{t("offline.storage_unavailable")}</p> : null}
        {drafts.length === 0 ? <p className="muted">{t("offline.no_drafts")}</p> : null}
        {drafts.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>{t("offline.draft")}</th>
                <th>{t("inspections.vehicle")}</th>
                <th>{t("inspections.performed_at")}</th>
                <th>{t("offline.files")}</th>
                <th>{t("offline.sync")}</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={d.mode === "new" ? `${base}/inspections/drafts/${d.id}` : `${base}/inspections/${d.inspectionId}`}>
                      {d.mode === "new"
                        ? d.data.kind
                          ? t(`inspections.kind_${d.data.kind}`)
                          : t("offline.kind_not_set")
                        : t("offline.edit_of_inspection")}
                    </Link>
                    <ExpiryNote draft={d} zone={zone} />
                  </td>
                  <td>{d.mode === "new" ? vehicleLabel(d, vehicles?.items) : "—"}</td>
                  <td>{d.mode === "new" ? formatMoment(d.data.performedAt, zone, i18n.language) : "—"}</td>
                  <td>{d.files.length}</td>
                  <td>
                    <SyncBadge state={d.state} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {canCreate ? (
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" className="secondary" disabled={creating} onClick={() => void start()}>
              {t("offline.start_offline")}
            </button>
          </div>
        ) : null}
        {drafts.length ? <StorageNotice scope={scope} /> : null}
      </div>
    </Card>
  );
}

function ExpiryNote({ draft, zone }: { draft: Draft; zone: string }) {
  const { t, i18n } = useTranslation();
  if (expiry(draft) === "ok" || draft.state === "synced") return null;
  return (
    <div className="alert warn small">
      {t("offline.expires_soon", { when: formatMoment(expiresAt(draft).toISOString(), zone, i18n.language) })}
    </div>
  );
}

/** Two-step deletion of an unsynced draft: the warning first (§ 10.4.4). */
function DeleteDraft({ scope, draft, onDeleted }: { scope: Scope; draft: Draft; onDeleted: () => void }) {
  const { t } = useTranslation();
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button type="button" className="secondary" onClick={() => setAsking(true)}>
        {t("offline.delete")}
      </button>
    );
  }
  return (
    <div className="stack">
      <p className="alert warn">{t("offline.delete_warning", { files: draft.files.length })}</p>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          type="button"
          className="danger"
          onClick={() => void deleteDraft(scope, draft.id).then(onDeleted)}
        >
          {t("offline.delete_confirm")}
        </button>
        <button type="button" className="secondary" onClick={() => setAsking(false)}>
          {t("offline.cancel")}
        </button>
      </div>
    </div>
  );
}

function transportOf(vehicle: OfflineVehicle | undefined): "car" | "motorcycle" {
  return vehicle && MOTORCYCLE_TYPES.includes(vehicle.vehicle_type) ? "motorcycle" : "car";
}

/** The vehicle a typed internal ID or plate names in the given list. */
export function matchVehicle<T extends { id: string; vehicle_id: string; plate: string }>(
  text: string,
  vehicles: T[],
): T | undefined {
  const norm = (value: string) => value.replace(/[\s-]/g, "").toUpperCase();
  const wanted = norm(text);
  if (!wanted) return undefined;
  return vehicles.find((v) => norm(v.vehicle_id) === wanted || (v.plate && norm(v.plate) === wanted));
}

/** The page is the only writer of the draft's data: the sending part changes it through it. */
type DraftEdit = {
  data: NewDraftData;
  set: (patch: Partial<NewDraftData>) => void;
  flush: () => Promise<NewDraft | null>;
};

/** The form of a new local draft; works the same with and without connection. */
export function DraftEditor({
  scope,
  draftId,
  base,
  zone,
  children,
}: {
  scope: Scope;
  draftId: string;
  base: string;
  zone: string;
  /** The sending part, only with connection. */
  children?: (draft: NewDraft, edit: DraftEdit) => ReactNode;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const online = useOnline();
  const draft = useDraft(scope, draftId);
  if (draft === undefined) return <Loading />;
  if (draft === null || draft.mode !== "new") {
    return (
      <div className="stack">
        <p className="alert warn">{t("offline.draft_missing")}</p>
        <p>
          <Link to={`${base}/inspections`}>← {t("inspections.title")}</Link>
        </p>
      </div>
    );
  }
  return (
    <NewDraftForm key={draft.id} scope={scope} draft={draft} base={base} zone={zone} online={online} onDeleted={() => navigate(`${base}/inspections`, { replace: true })}>
      {children}
    </NewDraftForm>
  );
}

function NewDraftForm({
  scope,
  draft,
  base,
  zone,
  online,
  onDeleted,
  children,
}: {
  scope: Scope;
  draft: NewDraft;
  base: string;
  zone: string;
  online: boolean;
  onDeleted: () => void;
  children?: (draft: NewDraft, edit: DraftEdit) => ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const positionName = usePositionName();
  const vehicles = useDataset<OfflineVehicle>(scope, "vehicles");
  const templates = useDataset<OfflineTemplate>(scope, "templates");
  // Typing is kept in the page and written to the device shortly after.
  const [data, setData] = useState<NewDraftData>(draft.data);
  const dataRef = useRef(data);
  const timer = useRef<number | null>(null);
  const [storageError, setStorageError] = useState(false);
  const busy = draft.state === "uploading";

  async function flush(): Promise<NewDraft | null> {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    try {
      return await updateDraft<NewDraft>(scope, draft.id, (current) =>
        JSON.stringify(current.data) === JSON.stringify(dataRef.current)
          ? current
          : transition({ ...current, data: dataRef.current }, { type: "changed" }),
      );
    } catch {
      setStorageError(true);
      return null;
    }
  }

  function set(patch: Partial<NewDraftData>) {
    const next = { ...dataRef.current, ...patch };
    dataRef.current = next;
    setData(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), 300);
  }

  useEffect(
    () => () => {
      if (timer.current !== null) void flush();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const list = vehicles?.items ?? [];
  const vehicle = list.find((v) => v.id === data.vehicleCardId) ?? matchVehicle(data.vehicleText, list);
  const transport = transportOf(vehicle);
  const template =
    (templates?.items ?? []).find((tpl) => tpl.id === data.templateId) ??
    (templates?.items ?? []).find((tpl) => tpl.transport === transport);
  const positions = (template?.positions ?? []).filter((p) => p.enabled);
  const photos = draft.files.filter((f) => f.kind === "photo");
  const extra = photos.filter((f) => !f.position || !positions.some((p) => p.code === f.position));
  const videos = draft.files.filter((f) => f.kind === "video");
  const now = toLocalInput(new Date(), zone);

  async function keep(file: File, kind: "photo" | "video", position: string | null) {
    try {
      // The template giving the positions is remembered with the first photo by a position.
      if (position && template && dataRef.current.templateId !== template.id) set({ templateId: template.id });
      await flush();
      await addFile(scope, draft.id, file, { kind, position, name: file.name });
    } catch {
      setStorageError(true);
    }
  }

  return (
    <>
      <h1>
        {t("offline.new_title")} <SyncBadge state={draft.state} />
      </h1>
      <p className="alert warn">{t("offline.not_conducted")}</p>
      {online ? null : <p className="muted small">{t("offline.offline_only_draft")}</p>}
      {storageError ? <p className="alert error">{t("offline.storage_unavailable")}</p> : null}
      <ExpiryNote draft={draft} zone={zone} />
      <fieldset disabled={busy} className="plain">
        <Card>
          <div className="stack">
            <Field label={t("inspections.kind")}>
              <select value={data.kind} onChange={(e) => set({ kind: e.target.value as InspectionKind })}>
                <option value="">{t("inspections.choose")}</option>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t(`inspections.kind_${k}`)}
                  </option>
                ))}
              </select>
            </Field>
            {children ? null : (
            <Field label={t("inspections.vehicle")} hint={vehicles ? t("offline.vehicles_saved", { when: formatMoment(vehicles.savedAt, zone, i18n.language) }) : t("offline.no_vehicles")}>
              <select value={data.vehicleCardId} onChange={(e) => set({ vehicleCardId: e.target.value })}>
                <option value="">{t("offline.vehicle_typed")}</option>
                {list.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} · {v.vehicle_id}
                    {v.plate ? ` · ${v.plate}` : ""}
                  </option>
                ))}
              </select>
            </Field>
            )}
            {data.vehicleCardId || children ? null : (
              <Field label={t("offline.vehicle_text")} hint={t("offline.vehicle_text_hint")}>
                <input value={data.vehicleText} maxLength={60} onChange={(e) => set({ vehicleText: e.target.value })} />
              </Field>
            )}
            <Field label={t("inspections.performed_at")} hint={t("inspections.performed_at_hint", { zone })}>
              <input
                type="datetime-local"
                max={now}
                value={toLocalInput(new Date(data.performedAt), zone)}
                onChange={(e) => e.target.value && set({ performedAt: fromLocalInput(e.target.value, zone) })}
              />
            </Field>
            <p className="muted small">{t("offline.place_later")}</p>
          </div>
        </Card>
        <Card title={t("inspections.readings")}>
          <div className="row">
            <Field label={t("inspections.odometer", { unit: vehicle?.odometer_unit ?? "" })}>
              <input inputMode="decimal" value={data.odometer} onChange={(e) => set({ odometer: e.target.value })} />
            </Field>
            <Field label={t("inspections.fuel")}>
              <input inputMode="decimal" value={data.fuel} onChange={(e) => set({ fuel: e.target.value })} />
            </Field>
          </div>
          <Field label={t("inspections.cleanliness")} hint={t("inspections.cleanliness_hint")}>
            <input value={data.cleanliness} maxLength={200} onChange={(e) => set({ cleanliness: e.target.value })} />
          </Field>
        </Card>
        <Card title={t("inspections.photos")}>
          {templates?.items.length ? (
            <Field label={t("inspections.template")}>
              <select value={template?.id ?? ""} onChange={(e) => set({ templateId: e.target.value })}>
                {templates.items.map((tpl) => (
                  <option key={tpl.id} value={tpl.id}>
                    {t(`inspections.transport_${tpl.transport}`)} · v{tpl.version}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <p className="muted small">{t("offline.no_templates")}</p>
          )}
          {positions.map((p) => {
            const taken = photos.filter((f) => f.position === p.code);
            return (
              <div key={p.code} className="position">
                <div className="position-head">
                  <strong>{positionName(p)}</strong>
                  <span className={`badge ${taken.length >= p.count ? "ok" : p.required ? "warn" : ""}`}>
                    {taken.length} / {p.count}
                  </span>
                  {p.required ? <span className="muted small">{t("inspections.position_required")}</span> : null}
                </div>
                <div className="media-row">
                  {taken.map((f) => (
                    <LocalThumb key={f.id} scope={scope} file={f} onRemove={busy ? undefined : () => void removeFile(scope, draft.id, f.id)} />
                  ))}
                </div>
                <PhotoInput compact local onLocal={(file) => keep(file, "photo", p.code)} disabled={busy} />
              </div>
            );
          })}
          <div className="position">
            <div className="position-head">
              <strong>{t("inspections.extra_photos")}</strong>
            </div>
            <div className="media-row">
              {extra.map((f) => (
                <LocalThumb key={f.id} scope={scope} file={f} onRemove={busy ? undefined : () => void removeFile(scope, draft.id, f.id)} />
              ))}
            </div>
            <PhotoInput compact local onLocal={(file) => keep(file, "photo", null)} disabled={busy} />
          </div>
        </Card>
        <Card title={t("inspections.videos")}>
          <p className="muted small">{t("inspections.videos_hint")}</p>
          <div className="media-row">
            {videos.map((f) => (
              <LocalThumb key={f.id} scope={scope} file={f} size={140} onRemove={busy ? undefined : () => void removeFile(scope, draft.id, f.id)} />
            ))}
          </div>
          <VideoInput local onLocal={(file) => keep(file, "video", null)} disabled={busy} />
        </Card>
      </fieldset>
      {children ? children(draft, { data, set, flush }) : (
        <Card title={t("offline.send_title")}>
          <p className="muted">{t("offline.send_when_online")}</p>
        </Card>
      )}
      <Card>
        <div className="stack">
          <StorageNotice scope={scope} />
          {busy ? null : <DeleteDraft scope={scope} draft={draft} onDeleted={onDeleted} />}
        </div>
      </Card>
      <p>
        <Link to={`${base}/inspections`}>← {t("inspections.title")}</Link>
      </p>
    </>
  );
}

/** The local draft in the company with connection: the form and its sending. */
export function OnlineDraftPage() {
  const { draftId = "" } = useParams();
  const company = useCompany();
  const scope = useScope();
  const online = useOnline();
  // Without connection the form is the same as in the offline interface; linking and sending
  // come back with the connection.
  return (
    <div className="page inspection-page">
      <DraftEditor scope={scope} draftId={draftId} base={companyPath(company)} zone={company.timezone ?? "UTC"}>
        {online ? (draft, edit) => <SendDraft scope={scope} draft={draft} edit={edit} /> : undefined}
      </DraftEditor>
    </div>
  );
}

/** § 10.4.3: after the connection is back the employee confirms the sign-in, links the draft
 * with the vehicle and the needed operation and sends it. */
function SendDraft({ scope, draft, edit }: { scope: Scope; draft: NewDraft; edit: DraftEdit }) {
  const { t, i18n } = useTranslation();
  const company = useCompany();
  const can = useCan();
  const navigate = useNavigate();
  const refresh = useRefreshInspections();
  const online = useOnline();
  const me = useMe();
  const vehicles = useVehicles();
  const rentals = useRentals(can("rental.view"));
  const operations = useServiceOperations("", can("service.view"));
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const live = useMemo(
    () =>
      (vehicles.data?.vehicles ?? []).map((v) => ({
        id: v.id,
        vehicle_id: v.vehicle_id,
        plate: v.characteristics.plate ?? "",
        name: v.name,
      })),
    [vehicles.data],
  );
  const data = edit.data;
  const vehicleId = data.vehicleCardId || matchVehicle(data.vehicleText, live)?.id || "";
  const ownRentals = (rentals.data?.rentals ?? []).filter((r) => r.vehicle_card_id === vehicleId && r.state !== "cancelled");
  const ownOperations = (operations.data?.operations ?? []).filter(
    (o) => o.vehicle_card_id === vehicleId && !["cancelled", "terminated"].includes(o.state),
  );
  const user = me.data;
  const name = user ? `${user.first_name} ${user.last_name}`.trim() || user.email || user.public_id : "";

  function link(patch: Partial<NewDraftData>) {
    edit.set(patch);
    setResult(null);
  }

  async function send(duplicatesConfirmed = false) {
    setResult(null);
    // The session is checked on the server first: without it the employee signs in again
    // (§ 10.4.3); the draft stays on the device meanwhile.
    const session = await me.refetch();
    if (!session.data) return;
    if (!data.vehicleCardId && vehicleId) edit.set({ vehicleCardId: vehicleId });
    if (!(await edit.flush())) return;
    setProgress(0);
    const outcome = await syncDraft(scope, draft.id, { duplicatesConfirmed, onProgress: setProgress });
    setProgress(null);
    setResult(outcome);
    if (outcome.status === "synced") {
      await refresh();
      navigate(companyPath(company, `/inspections/${outcome.inspectionId}`), { replace: true });
    }
  }

  const kinds = KINDS_OF[data.application];
  const blocked =
    !vehicleId ||
    !data.kind ||
    !kinds.includes(data.kind) ||
    (data.application === "rental" && !data.rentalId) ||
    (data.application === "service" && !data.serviceOperationId);
  const conflict = draft.state === "conflict" ? draft.conflict : null;
  const errorCode = result?.status === "error" ? result.code : draft.state === "error" ? draft.errorCode : null;
  const busy = draft.state === "uploading" || progress !== null;

  return (
    <Card title={t("offline.send_title")}>
      <fieldset disabled={busy} className="plain">
        <div className="stack">
          <Field label={t("inspections.vehicle")} hint={data.vehicleText && !data.vehicleCardId ? t("offline.typed_was", { text: data.vehicleText }) : undefined}>
            <select value={vehicleId} onChange={(e) => link({ vehicleCardId: e.target.value, rentalId: "", serviceOperationId: "" })}>
              <option value="">{t("inspections.choose")}</option>
              {(vehicles.data?.vehicles ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {v.vehicle_id}
                  {v.characteristics.plate ? ` · ${v.characteristics.plate}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("inspections.application")}>
            <select
              value={data.application}
              onChange={(e) => link({ application: e.target.value as InspectionApplication, rentalId: "", serviceOperationId: "" })}
            >
              {APPLICATIONS.map((a) => (
                <option key={a} value={a}>
                  {t(`inspections.application_${a}`)}
                </option>
              ))}
            </select>
          </Field>
          {data.application === "rental" ? (
            <Field label={t("inspections.rental")}>
              <select value={data.rentalId} onChange={(e) => link({ rentalId: e.target.value })}>
                <option value="">{t("inspections.choose")}</option>
                {ownRentals.map((r) => (
                  <option key={r.id} value={r.id}>
                    {t(`rentals.state_${r.state}`)} · {r.actual_start ? r.actual_start.slice(0, 10) : "—"}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {data.application === "service" ? (
            <Field label={t("inspections.service_operation")}>
              <select value={data.serviceOperationId} onChange={(e) => link({ serviceOperationId: e.target.value })}>
                <option value="">{t("inspections.choose")}</option>
                {ownOperations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {t("service.operation_title", { number: o.number })} · {t(`service.state_${o.state}`)}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {data.kind && !kinds.includes(data.kind) ? <p className="alert warn">{t("offline.kind_not_for_application")}</p> : null}
          <label>
            <input type="checkbox" checked={confirmed} onChange={() => setConfirmed(!confirmed)} />{" "}
            {t("offline.confirm_account", { name })}
          </label>
          <p className="muted small">{t("offline.send_hint")}</p>
        </div>
      </fieldset>

      {conflict?.type === "duplicate" ? (
        <div className="stack">
          <p className="alert warn">{t("offline.duplicates")}</p>
          <ul>
            {conflict.inspections.map((i) => (
              <li key={i.id}>
                <Link to={companyPath(company, `/inspections/${i.id}`)} target="_blank">
                  {formatMoment(i.performed_at, company.timezone ?? "UTC", i18n.language)}
                </Link>{" "}
                <span className={`badge ${stateBadge(i.state)}`}>{t(`inspections.state_${i.state}`)}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">{t("offline.duplicates_hint")}</p>
          <div className="actions" style={{ marginTop: 0 }}>
            <button type="button" disabled={busy || !online || !confirmed} onClick={() => void send(true)}>
              {t("offline.send_anyway")}
            </button>
          </div>
        </div>
      ) : null}
      {result?.status === "waiting" || (draft.state === "waiting" && !result) ? (
        <p className="alert warn">{t("offline.waiting_hint")}</p>
      ) : null}
      {errorCode ? (
        isSessionLost(errorCode) ? (
          <p className="alert error">{t("offline.sign_in_again")}</p>
        ) : (
          <ErrorMessage error={new ApiError(400, { code: errorCode, message_key: "", params: {}, field: null, request_id: "" })} />
        )
      ) : null}
      {progress !== null ? (
        <p className="muted small">{t("inspections.uploading", { percent: Math.round(progress * 100) })}</p>
      ) : null}
      {conflict?.type === "duplicate" ? null : (
        <div className="actions">
          <button type="button" disabled={busy || blocked || !online || !confirmed} onClick={() => void send(false)}>
            {busy ? t("offline.sending") : t("offline.send")}
          </button>
        </div>
      )}
      {!online ? <p className="muted small">{t("offline.send_when_online")}</p> : null}
    </Card>
  );
}
