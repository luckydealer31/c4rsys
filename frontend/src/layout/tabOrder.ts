import { useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { ME_KEY, useMe } from "../auth";

/** The order of the tabs of a section that the user puts for themself (customer's decisions of
 * 17.11.2026). It is remembered for the user, not for the device: saved in the profile. A tab
 * the saved order does not know — a new one — stands after the known ones, in its usual place
 * among such. */
export function useTabOrder<T extends string>(section: string, tabs: T[]) {
  const me = useMe();
  const client = useQueryClient();
  const update = useCommand<{ tab_orders: Record<string, string[]> }, { user: User }>("/me", "PATCH");
  const saved = me.data?.tab_orders?.[section] ?? [];
  const place = (key: T) => (saved.includes(key) ? saved.indexOf(key) : saved.length + tabs.indexOf(key));
  const ordered = [...tabs].sort((a, b) => place(a) - place(b));

  // The orders are sent one after another: the last one put waits for the answer to the one
  // before it, and an answer does not bring an older order back over a newer one.
  const waiting = useRef<string[] | null>(null);
  const sending = useRef(false);

  async function send() {
    if (sending.current) return;
    sending.current = true;
    try {
      while (waiting.current) {
        const order = waiting.current;
        waiting.current = null;
        const result = await update.run({ tab_orders: { [section]: order } });
        if (waiting.current) continue;
        if (result) client.setQueryData(ME_KEY, result.user);
        else void client.invalidateQueries({ queryKey: ME_KEY });
      }
    } finally {
      sending.current = false;
    }
  }

  /** Keeps the new order of the tabs shown now. */
  function save(next: T[]) {
    const user = client.getQueryData<User | null>(ME_KEY);
    if (!user || next.join() === ordered.join()) return;
    // The tabs not shown now (without their right or module) keep their saved places at the end.
    const order = [...next, ...saved.filter((key) => !tabs.some((tab) => tab === key))];
    // Shown at once; the answer of the server confirms it.
    client.setQueryData(ME_KEY, { ...user, tab_orders: { ...user.tab_orders, [section]: order } });
    waiting.current = order;
    void send();
  }

  return { ordered, save };
}
