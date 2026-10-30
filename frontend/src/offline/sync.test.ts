import "fake-indexeddb/auto";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { clearAll } from "./db";
import { addFile, getDraft, makeEditDraft, makeNewDraft, saveDraft, type Scope } from "./drafts";
import { syncDraft } from "./sync";

const calls = vi.hoisted(() => ({
  api: vi.fn(),
  upload: vi.fn(),
}));

vi.mock("../api/client", async (original) => {
  const real = await original<typeof import("../api/client")>();
  return { ...real, api: calls.api, uploadWithProgress: calls.upload };
});

const A: Scope = { userId: "USR-A", companyId: "CMP-1" };

function refusal(status: number, code: string, params: Record<string, unknown> = {}): ApiError {
  return new ApiError(status, { code, message_key: "", params, field: null, request_id: "" });
}

async function linkedDraft() {
  const draft = await saveDraft(
    makeNewDraft(A, { kind: "out_of_rental", vehicleCardId: "veh-1", odometer: "1200" }),
  );
  await addFile(A, draft.id, new Blob(["img"], { type: "image/jpeg" }), { kind: "photo", position: "front", name: "a.jpg" });
  return draft.id;
}

beforeEach(async () => {
  await clearAll();
  calls.api.mockReset();
  calls.upload.mockReset();
});

describe("sending a new draft (§ 10.4.3)", () => {
  it("uploads each file once, creates the inspection with the draft's id and deletes the copy", async () => {
    const id = await linkedDraft();
    calls.upload.mockResolvedValue({ id: "file-1", content_type: "image/jpeg" });
    calls.api.mockRejectedValueOnce(new ApiError(0, null));

    // No connection at the creation: the uploaded file is remembered, the draft waits.
    expect((await syncDraft(A, id)).status).toBe("waiting");
    const waiting = await getDraft(A, id);
    expect([waiting?.state, waiting?.files[0]?.serverFileId]).toEqual(["waiting", "file-1"]);

    calls.api.mockResolvedValueOnce({ inspection: { id: "insp-1", version: 1 } });
    const result = await syncDraft(A, id);
    expect(result).toMatchObject({ status: "synced", inspectionId: "insp-1" });
    expect(calls.upload).toHaveBeenCalledTimes(1);
    const [path, request] = calls.api.mock.calls[1] as [string, { body: Record<string, unknown>; idempotencyKey: string }];
    expect(path).toBe("/c/CMP-1/inspections");
    expect(request.body).toMatchObject({
      client_draft_id: id,
      vehicle_card_id: "veh-1",
      kind: "out_of_rental",
      odometer: "1200",
      place_kind: "location",
      place_location_id: null,
      files: [{ file_id: "file-1", kind: "photo", position: "front" }],
      duplicates_confirmed: false,
    });
    expect(request.idempotencyKey).toContain(id);
    expect(await getDraft(A, id)).toBeNull();
  });

  it("a draft sent earlier is not created twice", async () => {
    const id = await linkedDraft();
    calls.upload.mockResolvedValue({ id: "file-1", content_type: "image/jpeg" });
    calls.api.mockRejectedValueOnce(refusal(409, "OFFLINE_DRAFT_ALREADY_SENT", { inspection_id: "insp-9" }));
    expect(await syncDraft(A, id)).toMatchObject({ status: "synced", inspectionId: "insp-9" });
    expect(await getDraft(A, id)).toBeNull();
  });

  it("possible duplicates are a conflict until the employee sends it anyway", async () => {
    const id = await linkedDraft();
    calls.upload.mockResolvedValue({ id: "file-1", content_type: "image/jpeg" });
    const duplicates = [{ id: "insp-2", state: "saved", performed_at: "2026-10-30T09:00:00+00:00" }];
    calls.api.mockRejectedValueOnce(refusal(409, "INSPECTION_POSSIBLE_DUPLICATE", { inspections: duplicates }));
    expect(await syncDraft(A, id)).toEqual({ status: "conflict", conflict: { type: "duplicate", inspections: duplicates } });
    expect((await getDraft(A, id))?.state).toBe("conflict");
    // Not sent again by itself.
    expect((await syncDraft(A, id)).status).toBe("conflict");
    expect(calls.api).toHaveBeenCalledTimes(1);

    calls.api.mockResolvedValueOnce({ inspection: { id: "insp-3", version: 1 } });
    expect((await syncDraft(A, id, { duplicatesConfirmed: true })).status).toBe("synced");
    const body = (calls.api.mock.calls[1] as [string, { body: Record<string, unknown> }])[1].body;
    expect(body.duplicates_confirmed).toBe(true);
  });

  it("an ended session is an error and the draft stays", async () => {
    const id = await linkedDraft();
    calls.upload.mockRejectedValue(refusal(401, "SESSION_EXPIRED"));
    expect(await syncDraft(A, id)).toEqual({ status: "error", code: "SESSION_EXPIRED" });
    expect((await getDraft(A, id))?.errorCode).toBe("SESSION_EXPIRED");
  });
});

describe("sending changes of a server draft (§ 10.4.2)", () => {
  it("sends with the version it was made over and deletes the copy", async () => {
    const draft = await saveDraft(makeEditDraft(A, "insp-1", 4, { form: {}, body: { odometer: "5", files: [] } }));
    calls.api.mockResolvedValueOnce({ inspection: { id: "insp-1", version: 5 } });
    expect((await syncDraft(A, draft.id)).status).toBe("synced");
    const [path, request] = calls.api.mock.calls[0] as [string, { method: string; body: Record<string, unknown> }];
    expect([path, request.method, request.body.expected_version]).toEqual(["/c/CMP-1/inspections/insp-1", "PATCH", 4]);
    expect(await getDraft(A, draft.id)).toBeNull();
  });

  it("another server version or a saved inspection is a conflict, never overwritten", async () => {
    const draft = await saveDraft(makeEditDraft(A, "insp-1", 4, { form: {}, body: {} }));
    calls.api.mockRejectedValueOnce(refusal(409, "VERSION_CONFLICT"));
    expect(await syncDraft(A, draft.id)).toEqual({ status: "conflict", conflict: { type: "version" } });
    expect((await getDraft(A, draft.id))?.state).toBe("conflict");

    const saved = await saveDraft(makeEditDraft(A, "insp-2", 1, { form: {}, body: {} }));
    calls.api.mockRejectedValueOnce(refusal(422, "INSPECTION_NOT_EDITABLE"));
    expect(await syncDraft(A, saved.id)).toEqual({ status: "conflict", conflict: { type: "not_editable" } });
  });
});
