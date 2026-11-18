import { useSyncExternalStore } from "react";

const QUERY = "(max-width: 760px)";

function subscribe(changed: () => void) {
  const media = window.matchMedia?.(QUERY);
  media?.addEventListener("change", changed);
  return () => media?.removeEventListener("change", changed);
}

/** Whether the screen is as narrow as a phone: a wide table is shown there as blocks. */
export function useNarrow(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia?.(QUERY).matches ?? false,
    () => false,
  );
}
