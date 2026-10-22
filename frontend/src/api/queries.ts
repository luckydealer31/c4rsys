import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

/** A read of the API. Keys start with the company so a switch never mixes data (§ 3.3.8). */
export function useApi<T>(key: readonly unknown[], path: string, enabled = true) {
  // A refusal (no right, not found) is final: retrying it only delays showing the message.
  return useQuery({ queryKey: key, queryFn: () => api<T>(path), enabled, retry: false });
}
