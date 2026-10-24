import type { ContextType } from "./api/types";

/** Context types offered in the interface (customer's decisions of 23 and 24.10.2026): rental,
 * service and warehouse, even before the modules of service and warehouse are done. */
export const AVAILABLE_CONTEXTS: readonly ContextType[] = ["rental", "service", "warehouse"];

/** Types whose modules already work. A context of another type cannot be set up yet, so it is
 * shown as «module in development» instead of «needs setup». */
const WORKING_MODULES: readonly ContextType[] = ["rental", "warehouse"];

export function contextAvailable(type: ContextType): boolean {
  return AVAILABLE_CONTEXTS.includes(type);
}

export function moduleWorks(type: ContextType): boolean {
  return WORKING_MODULES.includes(type);
}
