/**
 * The local storage of the browser for offline work (ТЗ § 10.4.2–10.4.4): IndexedDB of this
 * origin. Stores:
 * - drafts — local drafts of inspections, by the scope «user | company»;
 * - blobs — photos and videos of the drafts as Blob, apart from the drafts so a list of drafts
 *   does not read the files;
 * - datasets — the lists needed to start an inspection offline (vehicles, templates), also by
 *   scope. API answers are never cached by the service worker.
 */

const NAME = "c4rsys-offline";
const VERSION = 1;

let opening: Promise<IDBDatabase> | null = null;

function promised<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function openDb(): Promise<IDBDatabase> {
  opening ??= new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB is not available"));
      return;
    }
    const request = indexedDB.open(NAME, VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      const drafts = db.createObjectStore("drafts", { keyPath: "id" });
      drafts.createIndex("scope", "scope");
      const blobs = db.createObjectStore("blobs", { keyPath: "id" });
      blobs.createIndex("draftId", "draftId");
      const datasets = db.createObjectStore("datasets", { keyPath: "key" });
      datasets.createIndex("userId", "userId");
    };
    request.onsuccess = () => {
      const db = request.result;
      // Another tab upgrading the database closes this connection; the next call reopens it.
      db.onversionchange = () => {
        db.close();
        opening = null;
      };
      resolve(db);
    };
    request.onerror = () => {
      opening = null;
      reject(request.error);
    };
  });
  return opening;
}

export type StoreName = "drafts" | "blobs" | "datasets";

/** One transaction over the given stores; resolves with the work's result after commit. */
export async function transact<T>(
  stores: StoreName[],
  mode: IDBTransactionMode,
  work: (tx: IDBTransaction) => Promise<T>,
): Promise<T> {
  const db = await openDb();
  const tx = db.transaction(stores, mode);
  const done = new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error("Transaction aborted"));
  });
  let result: T;
  try {
    result = await work(tx);
  } catch (error) {
    done.catch(() => undefined);
    try {
      tx.abort();
    } catch {
      // already finished
    }
    throw error;
  }
  await done;
  return result;
}

export function get<T>(tx: IDBTransaction, store: StoreName, key: IDBValidKey): Promise<T | undefined> {
  return promised(tx.objectStore(store).get(key) as IDBRequest<T | undefined>);
}

export function put(tx: IDBTransaction, store: StoreName, value: unknown): Promise<IDBValidKey> {
  return promised(tx.objectStore(store).put(value));
}

export function remove(tx: IDBTransaction, store: StoreName, key: IDBValidKey): Promise<undefined> {
  return promised(tx.objectStore(store).delete(key));
}

export function byIndex<T>(tx: IDBTransaction, store: StoreName, index: string, value: IDBValidKey): Promise<T[]> {
  return promised(tx.objectStore(store).index(index).getAll(value) as IDBRequest<T[]>);
}

export function all<T>(tx: IDBTransaction, store: StoreName): Promise<T[]> {
  return promised(tx.objectStore(store).getAll() as IDBRequest<T[]>);
}

/** Tests only: drops every record. */
export async function clearAll(): Promise<void> {
  await transact(["drafts", "blobs", "datasets"], "readwrite", async (tx) => {
    for (const store of ["drafts", "blobs", "datasets"] as const) {
      await promised(tx.objectStore(store).clear());
    }
  });
}
