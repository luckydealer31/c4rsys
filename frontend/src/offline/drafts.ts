/**
 * Local drafts of inspections (ТЗ § 10.4.2–10.4.4).
 *
 * - A draft belongs to one account and one company: every read takes the scope «user | company»
 *   and never returns a draft of another scope (§ 10.4.4). The interface reads drafts only for
 *   the signed-in user (or, offline, the user last signed in on this device, forgotten at the
 *   sign-out), so after the sign-out they are not shown until the same user signs in again.
 * - Two kinds: «new» — an inspection started without connection, with no official ID and no
 *   effect on the server until it is sent (§ 10.4.3); «edit» — changes of a server draft made
 *   with an unstable connection, sent automatically when the connection returns (§ 10.4.2).
 * - The sync state is always shown: local only, waiting, uploading, synced, conflict, error.
 *   A conflict is never resolved automatically (§ 10.4.2).
 * - After a successful sync the local copy is deleted; an unsynced one is kept 7 days after its
 *   last change and deleted at the next start of the application afterwards (§ 10.4.4).
 */
import type { InspectionApplication, InspectionKind } from "../api/types";
import { all, byIndex, get, put, remove, transact } from "./db";

export type Scope = { userId: string; companyId: string };

export function scopeKey(scope: Scope): string {
  return `${scope.userId}|${scope.companyId}`;
}

export type SyncState = "local" | "waiting" | "uploading" | "synced" | "conflict" | "error";
export const SYNC_STATES: readonly SyncState[] = ["local", "waiting", "uploading", "synced", "conflict", "error"];

export type DraftFile = {
  id: string;
  kind: "photo" | "video";
  /** A position of the template for a photo; null — an additional photo or a video. */
  position: string | null;
  name: string;
  type: string;
  size: number;
  /** The file on the server after its upload; the upload is not repeated. */
  serverFileId: string | null;
};

export type Duplicate = { id: string; state: string; performed_at: string | null };

export type Conflict =
  /** The server draft changed after the local changes were made. */
  | { type: "version" }
  /** The inspection was saved or ended on the server: it is never overwritten (§ 10.1.6). */
  | { type: "not_editable" }
  /** Inspections that may be the same one (§ 10.4.3). */
  | { type: "duplicate"; inspections: Duplicate[] };

export type NewDraftData = {
  kind: InspectionKind | "";
  /** A vehicle of the list loaded earlier, else what the employee typed: internal ID or plate. */
  vehicleCardId: string;
  vehicleText: string;
  /** The actual moment, ISO in UTC. */
  performedAt: string;
  odometer: string;
  fuel: string;
  cleanliness: string;
  /** The template whose positions were offered for photos. */
  templateId: string;
  /** Linking chosen before sending (§ 10.4.3). */
  application: InspectionApplication;
  rentalId: string;
  serviceOperationId: string;
  /** The employee looked at the possible duplicates and sends it anyway; a change asks again. */
  duplicatesConfirmed: boolean;
};

export type EditDraftData = {
  /** The form as the page shows it, to be restored. */
  form: unknown;
  /** The request of the change, without the files still local. */
  body: Record<string, unknown>;
};

type Base = {
  id: string;
  scope: string;
  userId: string;
  companyId: string;
  createdAt: string;
  updatedAt: string;
  /** Grows with every local change: a sync started before a change does not delete it. */
  revision: number;
  state: SyncState;
  errorCode: string | null;
  conflict: Conflict | null;
  files: DraftFile[];
};

export type NewDraft = Base & { mode: "new"; inspectionId: null; baseVersion: null; data: NewDraftData };
export type EditDraft = Base & { mode: "edit"; inspectionId: string; baseVersion: number; data: EditDraftData };
export type Draft = NewDraft | EditDraft;

export const KEEP_DAYS = 7;
/** PROPOSAL (agent): the warning of the automatic deletion is shown during the last day. */
export const WARN_DAYS = 6;
const DAY = 24 * 60 * 60 * 1000;

export function emptyNewData(now: Date = new Date()): NewDraftData {
  return {
    kind: "",
    vehicleCardId: "",
    vehicleText: "",
    performedAt: now.toISOString(),
    odometer: "",
    fuel: "",
    cleanliness: "",
    templateId: "",
    application: "standalone",
    rentalId: "",
    serviceOperationId: "",
    duplicatesConfirmed: false,
  };
}

function base(scope: Scope, now: Date, state: SyncState): Base {
  return {
    id: crypto.randomUUID(),
    scope: scopeKey(scope),
    userId: scope.userId,
    companyId: scope.companyId,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    revision: 1,
    state,
    errorCode: null,
    conflict: null,
    files: [],
  };
}

export function makeNewDraft(scope: Scope, data: Partial<NewDraftData> = {}, now: Date = new Date()): NewDraft {
  return {
    ...base(scope, now, "local"),
    mode: "new",
    inspectionId: null,
    baseVersion: null,
    data: { ...emptyNewData(now), ...data },
  };
}

export function makeEditDraft(
  scope: Scope,
  inspectionId: string,
  baseVersion: number,
  data: EditDraftData,
  now: Date = new Date(),
): EditDraft {
  return { ...base(scope, now, "waiting"), mode: "edit", inspectionId, baseVersion, data };
}

// --- Sync state ----------------------------------------------------------------------------------

export type SyncEvent =
  /** The employee changed the draft. */
  | { type: "changed" }
  /** Sending was asked for, by the employee or automatically. */
  | { type: "queue" }
  | { type: "start" }
  /** No connection: it waits for the connection. */
  | { type: "offline" }
  | { type: "success" }
  /** The server refused it (or the session ended): the employee has to act. */
  | { type: "refused"; code: string }
  | { type: "conflict"; conflict: Conflict }
  /** After a version conflict the employee keeps working with the draft over the new version. */
  | { type: "rebase"; version: number };

/** The next state of a draft. A conflict stays until the employee resolves it. */
export function transition<D extends Draft>(draft: D, event: SyncEvent, now: Date = new Date()): D {
  const next = { ...draft };
  switch (event.type) {
    case "changed":
      next.updatedAt = now.toISOString();
      next.revision = draft.revision + 1;
      if (draft.state !== "conflict") {
        // Changes of a server draft go automatically (§ 10.4.2); a new draft waits for the
        // employee to link it and send it (§ 10.4.3).
        next.state = draft.mode === "edit" ? "waiting" : "local";
        next.errorCode = null;
      }
      if (next.mode === "new") next.data = { ...next.data, duplicatesConfirmed: false };
      return next;
    case "queue":
      if (draft.state === "conflict" || draft.state === "uploading") return draft;
      return { ...next, state: "waiting", errorCode: null };
    case "start":
      return { ...next, state: "uploading", errorCode: null, conflict: null };
    case "offline":
      return { ...next, state: "waiting" };
    case "success":
      return { ...next, state: "synced", errorCode: null, conflict: null };
    case "refused":
      return { ...next, state: "error", errorCode: event.code };
    case "conflict":
      return { ...next, state: "conflict", conflict: event.conflict, errorCode: null };
    case "rebase":
      if (draft.mode !== "edit" || draft.state !== "conflict" || draft.conflict?.type !== "version") return draft;
      return { ...next, state: "local", conflict: null, baseVersion: event.version, updatedAt: now.toISOString() };
  }
}

// --- Keeping ---------------------------------------------------------------------------------------

export function expiresAt(draft: Pick<Draft, "updatedAt">): Date {
  return new Date(new Date(draft.updatedAt).getTime() + KEEP_DAYS * DAY);
}

export type Expiry = "ok" | "soon" | "expired";

export function expiry(draft: Pick<Draft, "updatedAt">, now: Date = new Date()): Expiry {
  const age = now.getTime() - new Date(draft.updatedAt).getTime();
  if (age >= KEEP_DAYS * DAY) return "expired";
  if (age >= WARN_DAYS * DAY) return "soon";
  return "ok";
}

// --- Storage ---------------------------------------------------------------------------------------

export const DRAFTS_CHANGED_EVENT = "c4r:drafts-changed";

/** Pages showing drafts read them again after a change, also in other tabs. */
function changed(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(DRAFTS_CHANGED_EVENT));
  try {
    const channel = new BroadcastChannel("c4r-drafts");
    channel.postMessage("changed");
    channel.close();
  } catch {
    // no channel in this browser
  }
}

type BlobRecord = { id: string; draftId: string; scope: string; blob: Blob };

function owned(draft: Draft | undefined, scope: Scope): draft is Draft {
  return draft !== undefined && draft.scope === scopeKey(scope);
}

/** Drafts of the scope, the latest change first. */
export async function listDrafts(scope: Scope): Promise<Draft[]> {
  const found = await transact(["drafts"], "readonly", (tx) => byIndex<Draft>(tx, "drafts", "scope", scopeKey(scope)));
  return found.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export async function getDraft(scope: Scope, id: string): Promise<Draft | null> {
  const found = await transact(["drafts"], "readonly", (tx) => get<Draft>(tx, "drafts", id));
  return owned(found, scope) ? found : null;
}

/** The local changes of a server inspection, if any. */
export async function editDraftOf(scope: Scope, inspectionId: string): Promise<EditDraft | null> {
  const drafts = await listDrafts(scope);
  return (drafts.find((d) => d.mode === "edit" && d.inspectionId === inspectionId) as EditDraft | undefined) ?? null;
}

export async function saveDraft(draft: Draft): Promise<Draft> {
  await transact(["drafts"], "readwrite", (tx) => put(tx, "drafts", draft));
  changed();
  void persistStorage();
  return draft;
}

/** Reads, changes and writes a draft in one transaction; null when it is not there. */
export async function updateDraft<D extends Draft>(
  scope: Scope,
  id: string,
  change: (draft: D) => D,
): Promise<D | null> {
  const result = await transact(["drafts"], "readwrite", async (tx) => {
    const found = await get<Draft>(tx, "drafts", id);
    if (!owned(found, scope)) return null;
    const next = change(found as D);
    await put(tx, "drafts", next);
    return next;
  });
  changed();
  return result;
}

export async function deleteDraft(scope: Scope, id: string): Promise<void> {
  await transact(["drafts", "blobs"], "readwrite", async (tx) => {
    const found = await get<Draft>(tx, "drafts", id);
    if (!owned(found, scope)) return;
    for (const blob of await byIndex<BlobRecord>(tx, "blobs", "draftId", id)) {
      await remove(tx, "blobs", blob.id);
    }
    await remove(tx, "drafts", id);
  });
  changed();
}

/** Adds a chosen photo or video to the draft: the file is kept as a Blob (§ 10.4.2). */
export async function addFile(
  scope: Scope,
  draftId: string,
  blob: Blob,
  meta: { kind: DraftFile["kind"]; position: string | null; name: string },
  now: Date = new Date(),
): Promise<Draft | null> {
  const file: DraftFile = {
    id: crypto.randomUUID(),
    kind: meta.kind,
    position: meta.position,
    name: meta.name,
    type: blob.type,
    size: blob.size,
    serverFileId: null,
  };
  const result = await transact(["drafts", "blobs"], "readwrite", async (tx) => {
    const found = await get<Draft>(tx, "drafts", draftId);
    if (!owned(found, scope)) return null;
    await put(tx, "blobs", { id: file.id, draftId, scope: found.scope, blob } satisfies BlobRecord);
    const next = transition({ ...found, files: [...found.files, file] }, { type: "changed" }, now);
    await put(tx, "drafts", next);
    return next;
  });
  changed();
  void persistStorage();
  return result;
}

export async function removeFile(scope: Scope, draftId: string, fileId: string, now: Date = new Date()): Promise<Draft | null> {
  const result = await transact(["drafts", "blobs"], "readwrite", async (tx) => {
    const found = await get<Draft>(tx, "drafts", draftId);
    if (!owned(found, scope)) return null;
    await remove(tx, "blobs", fileId);
    const next = transition({ ...found, files: found.files.filter((f) => f.id !== fileId) }, { type: "changed" }, now);
    await put(tx, "drafts", next);
    return next;
  });
  changed();
  return result;
}

export async function getBlob(scope: Scope, fileId: string): Promise<Blob | null> {
  const found = await transact(["blobs"], "readonly", (tx) => get<BlobRecord>(tx, "blobs", fileId));
  return found && found.scope === scopeKey(scope) ? found.blob : null;
}

/** The uploaded file is remembered, so a repeated sync does not upload it again. */
export async function markUploaded(scope: Scope, draftId: string, fileId: string, serverFileId: string): Promise<Draft | null> {
  return updateDraft(scope, draftId, (draft) => ({
    ...draft,
    files: draft.files.map((f) => (f.id === fileId ? { ...f, serverFileId } : f)),
  }));
}

export type Usage = { drafts: number; files: number; bytes: number };

/** The amount of local data of the scope: drafts and the sizes of their files (§ 10.4.4). */
export async function usage(scope: Scope): Promise<Usage> {
  const drafts = await listDrafts(scope);
  const files = drafts.flatMap((d) => d.files);
  return { drafts: drafts.length, files: files.length, bytes: files.reduce((sum, f) => sum + f.size, 0) };
}

/** Deletes the drafts kept longer than 7 days after the last change, of every account of the
 * device, except one being uploaded right now (§ 10.4.4). Returns how many were deleted. */
export async function purgeExpired(now: Date = new Date()): Promise<number> {
  const count = await transact(["drafts", "blobs"], "readwrite", async (tx) => {
    const drafts = await all<Draft>(tx, "drafts");
    let count = 0;
    for (const draft of drafts) {
      if (draft.state === "uploading" || expiry(draft, now) !== "expired") continue;
      for (const blob of await byIndex<BlobRecord>(tx, "blobs", "draftId", draft.id)) {
        await remove(tx, "blobs", blob.id);
      }
      await remove(tx, "drafts", draft.id);
      count += 1;
    }
    return count;
  });
  if (count) changed();
  return count;
}

/** Asks the browser not to evict the local data under storage pressure, within its abilities. */
export async function persistStorage(): Promise<boolean> {
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.persist) return false;
    if (await navigator.storage.persisted()) return true;
    return await navigator.storage.persist();
  } catch {
    return false;
  }
}

/** What the browser reports for this site as a whole (all local data, not only drafts). */
export async function storageEstimate(): Promise<{ usage: number; quota: number } | null> {
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.estimate) return null;
    const estimate = await navigator.storage.estimate();
    return { usage: estimate.usage ?? 0, quota: estimate.quota ?? 0 };
  } catch {
    return null;
  }
}

// --- Datasets for starting an inspection offline ------------------------------------------------

type DatasetRecord<T> = { key: string; scope: string; userId: string; savedAt: string; items: T[] };

export async function saveDataset<T>(scope: Scope, name: string, items: T[], now: Date = new Date()): Promise<void> {
  const record: DatasetRecord<T> = {
    key: `${scopeKey(scope)}|${name}`,
    scope: scopeKey(scope),
    userId: scope.userId,
    savedAt: now.toISOString(),
    items,
  };
  await transact(["datasets"], "readwrite", (tx) => put(tx, "datasets", record));
}

export async function loadDataset<T>(scope: Scope, name: string): Promise<{ savedAt: string; items: T[] } | null> {
  const found = await transact(["datasets"], "readonly", (tx) =>
    get<DatasetRecord<T>>(tx, "datasets", `${scopeKey(scope)}|${name}`),
  );
  return found ? { savedAt: found.savedAt, items: found.items } : null;
}

/** At the sign-out: the loaded lists are local working data and become unavailable (§ 10.4.2).
 * Drafts stay for the same account to send after it signs in again (§ 10.4.4). */
export async function clearDatasets(userId: string): Promise<void> {
  await transact(["datasets"], "readwrite", async (tx) => {
    for (const record of await byIndex<DatasetRecord<unknown>>(tx, "datasets", "userId", userId)) {
      await remove(tx, "datasets", record.key);
    }
  });
}
