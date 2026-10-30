import "fake-indexeddb/auto";

import { beforeEach, describe, expect, it } from "vitest";

import { clearAll } from "./db";
import {
  addFile,
  clearDatasets,
  deleteDraft,
  editDraftOf,
  expiry,
  getBlob,
  getDraft,
  listDrafts,
  loadDataset,
  makeEditDraft,
  makeNewDraft,
  markUploaded,
  purgeExpired,
  removeFile,
  saveDataset,
  saveDraft,
  transition,
  updateDraft,
  usage,
  type Scope,
} from "./drafts";

const A: Scope = { userId: "USR-A", companyId: "CMP-1" };
const A2: Scope = { userId: "USR-A", companyId: "CMP-2" };
const B: Scope = { userId: "USR-B", companyId: "CMP-1" };
const DAY = 24 * 60 * 60 * 1000;
const NOW = new Date("2026-10-30T10:00:00Z");

beforeEach(async () => {
  await clearAll();
});

describe("scope of drafts (§ 10.4.4)", () => {
  it("shows a draft only to the same account in the same company", async () => {
    const draft = await saveDraft(makeNewDraft(A, { vehicleText: "ABC 123" }));
    await saveDraft(makeNewDraft(A2));
    await saveDraft(makeNewDraft(B));

    expect((await listDrafts(A)).map((d) => d.id)).toEqual([draft.id]);
    expect(await getDraft(A, draft.id)).not.toBeNull();
    expect(await getDraft(A2, draft.id)).toBeNull();
    expect(await getDraft(B, draft.id)).toBeNull();
    expect(await updateDraft(B, draft.id, (d) => d)).toBeNull();

    // Another scope cannot delete it either.
    await deleteDraft(B, draft.id);
    expect(await getDraft(A, draft.id)).not.toBeNull();
  });

  it("keeps files as blobs of the draft's scope and counts their size", async () => {
    const draft = await saveDraft(makeNewDraft(A));
    const photo = new Blob(["12345"], { type: "image/jpeg" });
    const withFile = await addFile(A, draft.id, photo, { kind: "photo", position: "front", name: "a.jpg" });
    const file = withFile?.files[0];
    expect(file).toMatchObject({ kind: "photo", position: "front", size: 5, type: "image/jpeg", serverFileId: null });
    expect(await addFile(B, draft.id, photo, { kind: "photo", position: null, name: "b.jpg" })).toBeNull();

    const blob = await getBlob(A, file?.id ?? "");
    expect(await blob?.text()).toBe("12345");
    expect(await getBlob(B, file?.id ?? "")).toBeNull();
    expect(await usage(A)).toEqual({ drafts: 1, files: 1, bytes: 5 });
    expect(await usage(B)).toEqual({ drafts: 0, files: 0, bytes: 0 });

    const uploaded = await markUploaded(A, draft.id, file?.id ?? "", "server-file");
    expect(uploaded?.files[0]?.serverFileId).toBe("server-file");

    const without = await removeFile(A, draft.id, file?.id ?? "");
    expect(without?.files).toEqual([]);
    expect(await getBlob(A, file?.id ?? "")).toBeNull();
  });

  it("deletes the files with the draft", async () => {
    const draft = await saveDraft(makeNewDraft(A));
    const next = await addFile(A, draft.id, new Blob(["x"]), { kind: "video", position: null, name: "v.mp4" });
    await deleteDraft(A, draft.id);
    expect(await getDraft(A, draft.id)).toBeNull();
    expect(await getBlob(A, next?.files[0]?.id ?? "")).toBeNull();
  });

  it("finds the local changes of a server inspection", async () => {
    const edit = await saveDraft(makeEditDraft(A, "insp-1", 3, { form: {}, body: { odometer: "1" } }));
    expect((await editDraftOf(A, "insp-1"))?.id).toBe(edit.id);
    expect(await editDraftOf(A, "insp-2")).toBeNull();
    expect(await editDraftOf(B, "insp-1")).toBeNull();
  });

  it("forgets the loaded lists of the account at the sign-out, not its drafts", async () => {
    await saveDataset(A, "vehicles", [{ id: "v1" }]);
    await saveDataset(B, "vehicles", [{ id: "v2" }]);
    const draft = await saveDraft(makeNewDraft(A));
    expect((await loadDataset(A, "vehicles"))?.items).toEqual([{ id: "v1" }]);
    expect(await loadDataset(A2, "vehicles")).toBeNull();

    await clearDatasets("USR-A");
    expect(await loadDataset(A, "vehicles")).toBeNull();
    expect((await loadDataset(B, "vehicles"))?.items).toEqual([{ id: "v2" }]);
    expect(await getDraft(A, draft.id)).not.toBeNull();
  });
});

describe("keeping for 7 days (§ 10.4.4)", () => {
  it("warns during the last day and expires after 7 days since the last change", () => {
    const at = (days: number) => ({ updatedAt: new Date(NOW.getTime() - days * DAY).toISOString() });
    expect(expiry(at(5.9), NOW)).toBe("ok");
    expect(expiry(at(6), NOW)).toBe("soon");
    expect(expiry(at(6.99), NOW)).toBe("soon");
    expect(expiry(at(7), NOW)).toBe("expired");
  });

  it("a change starts the period again", () => {
    const old = makeNewDraft(A, {}, new Date(NOW.getTime() - 6.5 * DAY));
    expect(expiry(old, NOW)).toBe("soon");
    expect(expiry(transition(old, { type: "changed" }, NOW), NOW)).toBe("ok");
  });

  it("deletes expired drafts of every account at the start, except one being uploaded", async () => {
    const old = new Date(NOW.getTime() - 8 * DAY);
    const expired = await saveDraft(makeNewDraft(A, {}, old));
    await addFile(A, expired.id, new Blob(["x"]), { kind: "photo", position: null, name: "p" }, old);
    const other = await saveDraft(makeNewDraft(B, {}, old));
    const busy = await saveDraft({ ...makeNewDraft(A, {}, old), state: "uploading" });
    const fresh = await saveDraft(makeNewDraft(A, {}, new Date(NOW.getTime() - DAY)));

    expect(await purgeExpired(NOW)).toBe(2);
    expect(await getDraft(A, expired.id)).toBeNull();
    expect(await getDraft(B, other.id)).toBeNull();
    expect(await getDraft(A, busy.id)).not.toBeNull();
    expect(await getDraft(A, fresh.id)).not.toBeNull();
    expect((await usage(A)).files).toBe(0);
  });
});

describe("sync state (§ 10.4.2)", () => {
  it("a new draft stays local until it is sent", () => {
    let draft = makeNewDraft(A, {}, NOW);
    expect(draft.state).toBe("local");
    draft = transition(draft, { type: "changed" }, NOW);
    expect([draft.state, draft.revision]).toEqual(["local", 2]);
    draft = transition(draft, { type: "queue" });
    expect(draft.state).toBe("waiting");
    draft = transition(draft, { type: "start" });
    expect(draft.state).toBe("uploading");
    expect(transition(draft, { type: "queue" }).state).toBe("uploading");
    expect(transition(draft, { type: "offline" }).state).toBe("waiting");
    const refused = transition(draft, { type: "refused", code: "SESSION_EXPIRED" });
    expect([refused.state, refused.errorCode]).toEqual(["error", "SESSION_EXPIRED"]);
    // A change after a refusal makes it local again.
    expect(transition(refused, { type: "changed" })).toMatchObject({ state: "local", errorCode: null });
    expect(transition(draft, { type: "success" }).state).toBe("synced");
  });

  it("changes of a server draft wait for the connection to go automatically", () => {
    const draft = makeEditDraft(A, "insp-1", 2, { form: {}, body: {} }, NOW);
    expect(draft.state).toBe("waiting");
    const changed = transition({ ...draft, state: "error" as const, errorCode: "X" }, { type: "changed" });
    expect(changed).toMatchObject({ state: "waiting", errorCode: null });
  });

  it("a conflict stays until the employee resolves it", () => {
    const draft = makeEditDraft(A, "insp-1", 2, { form: {}, body: {} }, NOW);
    const conflict = transition(draft, { type: "conflict", conflict: { type: "version" } });
    expect(conflict.state).toBe("conflict");
    expect(transition(conflict, { type: "changed" }).state).toBe("conflict");
    expect(transition(conflict, { type: "queue" }).state).toBe("conflict");

    // Keeping the draft over the new version: it is sent only when the employee saves again.
    const kept = transition(conflict, { type: "rebase", version: 5 });
    expect([kept.state, kept.baseVersion, kept.conflict]).toEqual(["local", 5, null]);

    // A saved inspection is never overwritten: there is nothing to keep it over.
    const saved = transition(draft, { type: "conflict", conflict: { type: "not_editable" } });
    expect(transition(saved, { type: "rebase", version: 9 })).toBe(saved);
  });
});
