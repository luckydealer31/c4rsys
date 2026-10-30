/** React bindings of the local drafts (ТЗ § 10.4.2–10.4.4). */
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore } from "react";

import { api } from "../api/client";
import type { InspectionTemplate, Vehicle } from "../api/types";
import {
  DRAFTS_CHANGED_EVENT,
  getDraft,
  listDrafts,
  loadDataset,
  saveDataset,
  scopeKey,
  type Draft,
  type Scope,
} from "./drafts";
import { isClaimed, isRunning, syncDraft } from "./sync";

function subscribeOnline(callback: () => void): () => void {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

/** What the browser knows of the connection; a request may still fail while it says online. */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
}

function subscribeDrafts(callback: () => void): () => void {
  window.addEventListener(DRAFTS_CHANGED_EVENT, callback);
  let channel: BroadcastChannel | null = null;
  try {
    channel = new BroadcastChannel("c4r-drafts");
    channel.onmessage = callback;
  } catch {
    channel = null;
  }
  return () => {
    window.removeEventListener(DRAFTS_CHANGED_EVENT, callback);
    channel?.close();
  };
}

/** The scope of the local data: the signed-in account and the active company (§ 10.4.4). */
export const ScopeContext = createContext<Scope | null>(null);

export function useScope(): Scope {
  const scope = useContext(ScopeContext);
  if (!scope) throw new Error("Used outside of a company");
  return scope;
}

/** The drafts of the scope, read again on every change. */
export function useDrafts(scope: Scope): { drafts: Draft[] | null; error: boolean } {
  const [state, setState] = useState<{ key: string; drafts: Draft[] | null; error: boolean }>({
    key: "",
    drafts: null,
    error: false,
  });
  const key = scopeKey(scope);
  useEffect(() => {
    let alive = true;
    const read = () =>
      listDrafts(scope)
        .then((drafts) => alive && setState({ key, drafts, error: false }))
        .catch(() => alive && setState({ key, drafts: [], error: true }));
    void read();
    const stop = subscribeDrafts(() => void read());
    return () => {
      alive = false;
      stop();
    };
    // The scope is compared by its key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state.key === key ? { drafts: state.drafts, error: state.error } : { drafts: null, error: false };
}

/** One draft of the scope; null — not found (deleted, synced or of another scope). */
export function useDraft(scope: Scope, id: string): Draft | null | undefined {
  const [state, setState] = useState<{ id: string; draft: Draft | null | undefined }>({ id: "", draft: undefined });
  const key = scopeKey(scope);
  useEffect(() => {
    let alive = true;
    const read = () =>
      getDraft(scope, id)
        .then((draft) => alive && setState({ id, draft }))
        .catch(() => alive && setState({ id, draft: null }));
    void read();
    const stop = subscribeDrafts(() => void read());
    return () => {
      alive = false;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, id]);
  return state.id === id ? state.draft : undefined;
}

export type OfflineVehicle = {
  id: string;
  vehicle_id: string;
  name: string;
  plate: string;
  vehicle_type: string;
  body_type: string;
  odometer_unit: string;
};

export type OfflineTemplate = Pick<InspectionTemplate, "id" | "transport" | "version" | "positions">;

/** Only what an offline draft needs is kept: no owners, no clients. */
export function offlineVehicle(vehicle: Vehicle): OfflineVehicle {
  return {
    id: vehicle.id,
    vehicle_id: vehicle.vehicle_id,
    name: vehicle.name,
    plate: vehicle.characteristics.plate ?? "",
    vehicle_type: vehicle.characteristics.vehicle_type ?? "",
    body_type: vehicle.characteristics.body_type ?? "",
    odometer_unit: vehicle.odometer_unit,
  };
}

/** While the company is open with the connection, the lists for an offline start are saved in
 * the local storage of this account and company (§ 10.4.3: «from the list loaded earlier»). */
export function useOfflineDatasets(scope: Scope, enabled: boolean): void {
  const key = scopeKey(scope);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    void (async () => {
      try {
        const vehicles = await api<{ vehicles: Vehicle[] }>(`/c/${scope.companyId}/vehicles`, { background: true });
        if (alive) await saveDataset(scope, "vehicles", vehicles.vehicles.map(offlineVehicle));
        const templates = await api<{ templates: InspectionTemplate[] }>(`/c/${scope.companyId}/inspection-templates`, {
          background: true,
        });
        if (alive) {
          await saveDataset<OfflineTemplate>(
            scope,
            "templates",
            templates.templates.map((t) => ({ id: t.id, transport: t.transport, version: t.version, positions: t.positions })),
          );
        }
      } catch {
        // Without connection the lists saved earlier stay.
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
}

export function useDataset<T>(scope: Scope, name: string): { savedAt: string; items: T[] } | null | undefined {
  const [state, setState] = useState<{ key: string; value: { savedAt: string; items: T[] } | null | undefined }>({
    key: "",
    value: undefined,
  });
  const key = `${scopeKey(scope)}|${name}`;
  useEffect(() => {
    let alive = true;
    loadDataset<T>(scope, name)
      .then((value) => alive && setState({ key, value }))
      .catch(() => alive && setState({ key, value: null }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state.key === key ? state.value : undefined;
}

/** Sends waiting drafts in the background when the connection returns, at the start and every
 * minute (§ 10.4.2): changes of server drafts, and new drafts the employee already linked and
 * sent. A draft open on a page is sent by that page. */
export function useAutoSync(scope: Scope): void {
  const client = useQueryClient();
  const key = scopeKey(scope);
  const run = useCallback(async () => {
    if (!navigator.onLine) return;
    let drafts: Draft[];
    try {
      drafts = await listDrafts(scope);
    } catch {
      return;
    }
    let synced = false;
    for (const draft of drafts) {
      if (draft.state !== "waiting" || isClaimed(draft.id) || isRunning(draft.id)) continue;
      if (draft.mode === "new" && !draft.data.vehicleCardId) continue;
      const result = await syncDraft(scope, draft.id);
      if (result.status === "synced") synced = true;
      if (result.status === "waiting") break;
    }
    if (synced) {
      for (const name of ["inspections", "inspection", "vehicle-inspections"]) {
        await client.invalidateQueries({ queryKey: [name] });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client]);
  useEffect(() => {
    void run();
    const online = () => void run();
    window.addEventListener("online", online);
    const timer = window.setInterval(() => void run(), 60_000);
    return () => {
      window.removeEventListener("online", online);
      window.clearInterval(timer);
    };
  }, [run]);
}
