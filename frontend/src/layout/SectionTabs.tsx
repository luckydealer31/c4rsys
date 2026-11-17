import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";

import { MotionIcon } from "../components/motion/MotionIcon";
import { useTabOrder } from "./tabOrder";

/** The tabs of a section, one look everywhere, in the order the user has put (customer's
 * decisions of 17.11.2026). The order is changed in its own mode, switched on by the button at
 * the right end of the tabs: the tabs sway, and a tab is dragged to its place — by the mouse or
 * by a finger; the others make room at once. In that mode a tab is not opened by a press.
 * `section` — the name the order is kept under. */
export function SectionTabs<T extends string>({
  section,
  tabs,
  current,
  onSelect,
  label,
  centered = false,
  style,
}: {
  section: string;
  tabs: { key: T; text: string }[];
  current: T;
  onSelect: (key: T) => void;
  label?: string;
  centered?: boolean;
  style?: CSSProperties;
}) {
  const { t } = useTranslation();
  const order = useTabOrder(
    section,
    tabs.map((tab) => tab.key),
  );
  const list = useRef<HTMLDivElement>(null);
  const [ordering, setOrdering] = useState(false);
  // The button has been pressed here: from then on its drawing plays when it changes.
  const [pressed, setPressed] = useState(false);
  // While a tab is dragged: the tab and the order as it is shown under the pointer.
  const [dragged, setDragged] = useState<T | null>(null);
  const [draft, setDraft] = useState<T[] | null>(null);
  const shown = draft ?? order.ordered;
  // The same, as the events of the pointer read it: they may come faster than the page is
  // drawn again, and must not act on what was before.
  const drag = useRef<{ key: T; order: T[] } | null>(null);

  // «Escape» leaves the mode.
  useEffect(() => {
    if (!ordering) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOrdering(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ordering]);

  /** With `key` put at the place the pointer shows: after the tabs that stand before the
   * pointer — in the lines above it, or in its line with the middle to the left of it. */
  function placed(key: T, from: T[], x: number, y: number): T[] {
    const others = from.filter((other) => other !== key);
    let before = 0;
    for (const other of others) {
      const box = list.current?.querySelector(`[data-tab="${other}"]`)?.getBoundingClientRect();
      if (box && (box.bottom < y || (box.top <= y && box.left + box.width / 2 < x))) before += 1;
    }
    return [...others.slice(0, before), key, ...others.slice(before)];
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (!ordering) return;
    const key = (event.target as Element).closest<HTMLElement>("[data-tab]")?.dataset.tab as T | undefined;
    if (!key) return;
    event.preventDefault();
    // The list keeps the pointer: the tabs move under it. A pointer that is not a real one —
    // of a test — cannot be kept, and is followed without it.
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* followed without the capture */
    }
    drag.current = { key, order: shown };
    setDragged(key);
    setDraft(shown);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const now = drag.current;
    if (!now) return;
    const next = placed(now.key, now.order, event.clientX, event.clientY);
    if (next.join() === now.order.join()) return;
    drag.current = { key: now.key, order: next };
    setDraft(next);
  }

  function drop() {
    const now = drag.current;
    if (!now) return;
    drag.current = null;
    order.save(now.order);
    setDragged(null);
    setDraft(null);
  }

  /** Without a pointer: the arrows move the tab that has the focus. */
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, key: T) {
    if (!ordering || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    const from = shown.indexOf(key);
    const to = from + (event.key === "ArrowLeft" ? -1 : 1);
    if (to < 0 || to >= shown.length) return;
    const next = shown.filter((other) => other !== key);
    next.splice(to, 0, key);
    order.save(next);
  }

  if (tabs.length < 2) return null;
  const text = new Map(tabs.map((tab) => [tab.key, tab.text]));
  const action = t(ordering ? "common.tabs_order_done" : "common.tabs_order");
  return (
    <div className={`tabs-bar${centered ? " centered" : ""}`} style={style}>
      <div
        ref={list}
        className={`tabs left${ordering ? " ordering" : ""}`}
        role="tablist"
        aria-label={label}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={drop}
        onPointerCancel={drop}
        onLostPointerCapture={drop}
      >
        {shown.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            data-tab={key}
            aria-selected={current === key}
            className={[current === key ? "active" : "", dragged === key ? "dragging" : ""].filter(Boolean).join(" ")}
            onClick={() => (ordering ? undefined : onSelect(key))}
            onKeyDown={(event) => onKeyDown(event, key)}
          >
            {text.get(key)}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`icon-button tabs-order${ordering ? " primary" : ""}`}
        aria-pressed={ordering}
        aria-label={action}
        title={action}
        onClick={() => {
          setPressed(true);
          setOrdering(!ordering);
        }}
      >
        {/* The animated drawings the customer chose (decision of 17.11.2026): the two arrows,
            and the check mark while the order is being changed. */}
        <MotionIcon key={ordering ? "check" : "swap"} name={ordering ? "check" : "swap"} size={18} autoplay={pressed} />
      </button>
    </div>
  );
}
