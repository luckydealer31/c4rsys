import { useQueryClient } from "@tanstack/react-query";
import { useState, type DragEvent } from "react";

import type { User } from "../api/types";
import { useCommand } from "../api/useCommand";
import { ME_KEY, useMe } from "../auth";

/** The order of the tabs of a section that the user puts for themself by dragging a tab onto
 * the place of another (customer's decision of 17.11.2026). It is remembered for the user, not
 * for the device: saved in the profile. A tab the saved order does not know — a new one — stands
 * after the known ones, in its usual place among such. */
export function useTabOrder<T extends string>(section: string, tabs: T[]) {
  const me = useMe();
  const client = useQueryClient();
  const update = useCommand<{ tab_orders: Record<string, string[]> }, { user: User }>("/me", "PATCH");
  const [dragged, setDragged] = useState<T | null>(null);
  const [over, setOver] = useState<T | null>(null);
  const saved = me.data?.tab_orders?.[section] ?? [];
  const place = (key: T) => (saved.includes(key) ? saved.indexOf(key) : saved.length + tabs.indexOf(key));
  const ordered = [...tabs].sort((a, b) => place(a) - place(b));

  function move(from: T, to: T) {
    const user = me.data;
    if (from === to || !user) return;
    const next = ordered.filter((key) => key !== from);
    // The place of the other tab: dragged to the right it stands after it, to the left — before.
    next.splice(ordered.indexOf(to), 0, from);
    // The tabs not shown now (without their right or module) keep their saved places at the end.
    const order = [...next, ...saved.filter((key) => !tabs.some((tab) => tab === key))];
    // Shown at once; the answer of the server confirms it.
    client.setQueryData(ME_KEY, { ...user, tab_orders: { ...user.tab_orders, [section]: order } });
    void update.run({ tab_orders: { [section]: order } }).then((result) => {
      if (result) client.setQueryData(ME_KEY, result.user);
      else void client.invalidateQueries({ queryKey: ME_KEY });
    });
  }

  /** What makes a tab draggable and a place to drop another one. */
  const drag = (key: T) => ({
    draggable: true,
    "data-drop":
      dragged !== null && dragged !== key && over === key
        ? ordered.indexOf(dragged) < ordered.indexOf(key)
          ? "after"
          : "before"
        : undefined,
    onDragStart: (event: DragEvent) => {
      setDragged(key);
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", key);
    },
    onDragOver: (event: DragEvent) => {
      if (dragged === null) return;
      event.preventDefault();
      setOver(key);
    },
    onDragLeave: () => setOver((current) => (current === key ? null : current)),
    onDrop: (event: DragEvent) => {
      event.preventDefault();
      if (dragged !== null) move(dragged, key);
      setDragged(null);
      setOver(null);
    },
    onDragEnd: () => {
      setDragged(null);
      setOver(null);
    },
  });

  return { ordered, drag };
}
