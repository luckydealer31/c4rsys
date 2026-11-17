import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useLocation } from "react-router";

import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { ME_KEY, useMe } from "../auth";

/** The order of the tabs of a section that the user puts for themself (customer's decisions of
 * 17.11.2026). It is remembered for the user, not for the device: saved in the profile. A tab
 * the saved order does not know — a new one — stands after the known ones, in its usual place
 * among such. */
function inOrder<T extends string>(saved: string[], tabs: readonly T[]): T[] {
  const place = (key: T) => (saved.includes(key) ? saved.indexOf(key) : saved.length + tabs.indexOf(key));
  return [...tabs].sort((a, b) => place(a) - place(b));
}

/** The tab a section or a card opens on when its address names none: the first one in the
 * user's order (customer's decision of 17.11.2026) — the order would be of little use if the
 * section still opened on the usual tab. Returns a function, so that it may be called before
 * the tabs are known. */
export function useFirstTab(section: string) {
  const saved = useMe().data?.tab_orders?.[section] ?? [];
  // The order as it was when this address was opened: while the user changes the order, the tab
  // they are on stays; the new first tab is opened the next time the section is.
  const { key } = useLocation();
  const [opened, setOpened] = useState({ key, saved });
  if (opened.key !== key) setOpened({ key, saved });
  const order = opened.key === key ? opened.saved : saved;
  return <T extends string>(tabs: readonly T[]): T | undefined => inOrder(order, tabs)[0];
}

export function useTabOrder<T extends string>(section: string, tabs: T[]) {
  const me = useMe();
  const client = useQueryClient();
  const update = useCommand<{ tab_orders: Record<string, string[]> }, { user: User }>("/me", "PATCH");
  const saved = me.data?.tab_orders?.[section] ?? [];
  const ordered = inOrder(saved, tabs);

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
