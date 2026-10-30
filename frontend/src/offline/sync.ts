/**
 * Sending local drafts (ТЗ § 10.4.2–10.4.3). Files go first, each once (the server's file is
 * remembered in the draft and its upload key is the local file's id), then the inspection:
 * - a new draft is created on the server as a usual draft inspection with the draft's id, so a
 *   repeated sending never makes a second record; possible duplicates come back as a conflict;
 * - changes of a server draft are sent with the version they were made over; another version
 *   is a conflict, a saved inspection is never overwritten (§ 10.1.6).
 * No connection keeps the draft waiting; a refusal, including an ended session, is shown.
 */
import { api, ApiError, uploadWithProgress } from "../api/client";
import type { Inspection } from "../api/types";
import {
  deleteDraft,
  getBlob,
  getDraft,
  markUploaded,
  transition,
  updateDraft,
  type Conflict,
  type Draft,
  type Duplicate,
  type EditDraft,
  type NewDraft,
  type Scope,
} from "./drafts";

export type SyncResult =
  | { status: "synced"; inspection: Inspection | null; inspectionId: string; kept: boolean }
  | { status: "waiting" }
  | { status: "conflict"; conflict: Conflict }
  | { status: "error"; code: string }
  | { status: "busy" }
  | { status: "missing" };

/** Drafts being sent now, and those a page has open: the background sync leaves them alone. */
const running = new Set<string>();
const claimed = new Set<string>();

export function claim(draftId: string): () => void {
  claimed.add(draftId);
  return () => claimed.delete(draftId);
}

export function isClaimed(draftId: string): boolean {
  return claimed.has(draftId);
}

export function isRunning(draftId: string): boolean {
  return running.has(draftId);
}

const SESSION_CODES = ["SESSION_REQUIRED", "SESSION_EXPIRED"];

export function isSessionLost(code: string | null | undefined): boolean {
  return Boolean(code && SESSION_CODES.includes(code));
}

function failure(caught: unknown): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(0, null);
}

async function setState(scope: Scope, id: string, change: (draft: Draft) => Draft): Promise<Draft | null> {
  return updateDraft(scope, id, change);
}

/** Uploads the files of the draft not yet on the server. */
async function uploadFiles(scope: Scope, draft: Draft, onProgress?: (share: number) => void): Promise<Draft> {
  let current = draft;
  const waiting = draft.files.filter((f) => !f.serverFileId);
  const total = waiting.reduce((sum, f) => sum + f.size, 0) || 1;
  let done = 0;
  for (const file of waiting) {
    const blob = await getBlob(scope, file.id);
    if (!blob) throw new ApiError(400, { code: "OFFLINE_FILE_MISSING", message_key: "", params: {}, field: null, request_id: "" });
    const uploaded = await uploadWithProgress(
      file.kind === "photo" ? "inspection_photo" : "inspection_video",
      new File([blob], file.name || file.id, { type: file.type || blob.type }),
      (share) => onProgress?.((done + share * file.size) / total),
      `offline-file-${file.id}`,
    );
    done += file.size;
    current = (await markUploaded(scope, draft.id, file.id, uploaded.id)) ?? current;
  }
  return current;
}

function serverFiles(draft: Draft): { file_id: string; kind: string; position: string }[] {
  return draft.files
    .filter((f) => f.serverFileId)
    .map((f) => ({ file_id: f.serverFileId as string, kind: f.kind, position: f.position ?? "" }));
}

export function newDraftBody(draft: NewDraft, duplicatesConfirmed: boolean): Record<string, unknown> {
  const d = draft.data;
  const number = (value: string) => value.trim().replace(",", ".") || null;
  const application = d.application;
  return {
    client_draft_id: draft.id,
    duplicates_confirmed: duplicatesConfirmed,
    vehicle_card_id: d.vehicleCardId,
    kind: d.kind,
    application,
    rental_id: application === "rental" ? d.rentalId || null : null,
    service_operation_id: application === "service" ? d.serviceOperationId || null : null,
    template_id: d.templateId || null,
    performed_at: d.performedAt,
    // The place is the vehicle's location; it can be refined after the sync (§ 10.4.3).
    place_kind: "location",
    place_location_id: null,
    odometer: number(d.odometer),
    fuel: number(d.fuel),
    cleanliness: d.cleanliness.trim(),
    files: serverFiles(draft),
  };
}

function conflictOf(error: ApiError): Conflict | null {
  if (error.code === "VERSION_CONFLICT") return { type: "version" };
  if (error.code === "INSPECTION_NOT_EDITABLE" || error.code === "INSPECTION_NOT_DRAFT") return { type: "not_editable" };
  if (error.code === "INSPECTION_POSSIBLE_DUPLICATE") {
    return { type: "duplicate", inspections: (error.params.inspections as Duplicate[] | undefined) ?? [] };
  }
  return null;
}

/** Sends a draft of either kind. */
export async function syncDraft(
  scope: Scope,
  draftId: string,
  options: { duplicatesConfirmed?: boolean; onProgress?: (share: number) => void } = {},
): Promise<SyncResult> {
  if (running.has(draftId)) return { status: "busy" };
  running.add(draftId);
  try {
    const found = await getDraft(scope, draftId);
    if (!found) return { status: "missing" };
    if (found.state === "conflict" && !(found.conflict?.type === "duplicate" && options.duplicatesConfirmed)) {
      return { status: "conflict", conflict: found.conflict ?? { type: "version" } };
    }
    const started = await setState(scope, draftId, (d) => {
      const next = transition(d, { type: "start" });
      return next.mode === "new" && options.duplicatesConfirmed
        ? { ...next, data: { ...next.data, duplicatesConfirmed: true } }
        : next;
    });
    if (!started) return { status: "missing" };
    try {
      const uploaded = await uploadFiles(scope, started, options.onProgress);
      return uploaded.mode === "new"
        ? await sendNew(scope, uploaded, uploaded.data.duplicatesConfirmed)
        : await sendEdit(scope, uploaded);
    } catch (caught) {
      const error = failure(caught);
      if (error.status === 0) {
        await setState(scope, draftId, (d) => transition(d, { type: "offline" }));
        return { status: "waiting" };
      }
      const conflict = conflictOf(error);
      if (conflict) {
        await setState(scope, draftId, (d) => transition(d, { type: "conflict", conflict }));
        return { status: "conflict", conflict };
      }
      await setState(scope, draftId, (d) => transition(d, { type: "refused", code: error.code }));
      return { status: "error", code: error.code };
    }
  } finally {
    running.delete(draftId);
  }
}

async function sendNew(scope: Scope, draft: NewDraft, duplicatesConfirmed: boolean): Promise<SyncResult> {
  try {
    const result = await api<{ inspection: Inspection }>(`/c/${draft.companyId}/inspections`, {
      method: "POST",
      body: newDraftBody(draft, duplicatesConfirmed),
      idempotencyKey: `offline-${draft.id}-${draft.revision}-${duplicatesConfirmed ? 1 : 0}`,
    });
    await deleteDraft(scope, draft.id);
    return { status: "synced", inspection: result.inspection, inspectionId: result.inspection.id, kept: false };
  } catch (caught) {
    const error = failure(caught);
    // Sent earlier, the answer was lost: the inspection is on the server already.
    if (error.code === "OFFLINE_DRAFT_ALREADY_SENT") {
      await deleteDraft(scope, draft.id);
      return { status: "synced", inspection: null, inspectionId: String(error.params.inspection_id ?? ""), kept: false };
    }
    throw error;
  }
}

async function sendEdit(scope: Scope, draft: EditDraft): Promise<SyncResult> {
  const body = draft.data.body;
  const files = [...((body.files as unknown[] | undefined) ?? []), ...serverFiles(draft)];
  const result = await api<{ inspection: Inspection }>(`/c/${draft.companyId}/inspections/${draft.inspectionId}`, {
    method: "PATCH",
    body: { ...body, files, expected_version: draft.baseVersion },
    idempotencyKey: `offline-${draft.id}-${draft.revision}`,
  });
  // A change made while it was being sent stays and goes over the new version.
  let kept = false;
  await updateDraft<EditDraft>(scope, draft.id, (current) => {
    if (current.revision !== draft.revision) {
      kept = true;
      // The files just attached are on the server now; the later change carries them in its form.
      const sent = new Set(draft.files.filter((f) => f.serverFileId).map((f) => f.id));
      const attached = serverFiles({ ...current, files: current.files.filter((f) => sent.has(f.id)) });
      const known = new Set(((current.data.body.files as { file_id: string }[] | undefined) ?? []).map((f) => f.file_id));
      return {
        ...current,
        state: "waiting",
        baseVersion: result.inspection.version,
        files: current.files.filter((f) => !sent.has(f.id)),
        data: {
          ...current.data,
          body: {
            ...current.data.body,
            files: [
              ...((current.data.body.files as unknown[] | undefined) ?? []),
              ...attached.filter((f) => !known.has(f.file_id)),
            ],
          },
        },
      };
    }
    return current;
  });
  if (!kept) await deleteDraft(scope, draft.id);
  return { status: "synced", inspection: result.inspection, inspectionId: result.inspection.id, kept };
}
