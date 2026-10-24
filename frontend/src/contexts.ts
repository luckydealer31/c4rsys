import type { ContextType } from "./api/types";

/** Context types whose modules work in this version. A type is shown in the interface only
 * when its module is implemented (§ 4.1.5, § 4.1.9; customer's decision of 24.10.2026): service
 * and warehouse stay hidden until their modules are done; their existing contexts are kept. */
export const AVAILABLE_CONTEXTS: readonly ContextType[] = ["rental"];

export function contextAvailable(type: ContextType): boolean {
  return AVAILABLE_CONTEXTS.includes(type);
}
