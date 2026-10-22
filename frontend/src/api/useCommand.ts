import { useCallback, useRef, useState } from "react";

import { api, ApiError, type RequestOptions } from "./client";

/**
 * A business command with its Idempotency-Key (A07). The key lives as long as the same request is
 * being retried: a repeated click or a network retry with the same body reuses it, so the server
 * posts the operation once (acceptance criterion 4, E10). A changed body or a success starts a
 * new key.
 */
export function useCommand<TBody, TResult>(
  path: string | ((body: TBody) => string),
  method: RequestOptions["method"] = "POST",
) {
  const key = useRef<{ body: string; key: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const run = useCallback(
    async (body: TBody): Promise<TResult | null> => {
      const serialized = JSON.stringify(body);
      if (key.current?.body !== serialized) {
        key.current = { body: serialized, key: crypto.randomUUID() };
      }
      setPending(true);
      setError(null);
      try {
        const target = typeof path === "function" ? path(body) : path;
        const result = await api<TResult>(target, {
          method,
          body,
          idempotencyKey: key.current.key,
        });
        key.current = null;
        return result;
      } catch (caught) {
        // The key is kept: if the request did reach the server, a retry returns that result.
        setError(caught instanceof ApiError ? caught : new ApiError(0, null));
        return null;
      } finally {
        setPending(false);
      }
    },
    [path, method],
  );

  return { run, pending, error, clearError: () => setError(null) };
}
