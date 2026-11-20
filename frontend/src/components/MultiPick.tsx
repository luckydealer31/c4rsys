import { useCallback, useEffect, useRef, useState } from "react";

import { useDismiss } from "./useDismiss";

/** A list with marks for a choice of all, one or several (customer's decisions of 20.11.2026:
 * locations and warehouses may be dozens, so they are not laid out as buttons). `chosen` empty —
 * all. The button says what is chosen: all, the name of the only one, or how many. */
export function MultiPick({
  options,
  chosen,
  onChange,
  label,
  all,
  count,
  search,
}: {
  options: { id: string; name: string }[];
  chosen: Set<string>;
  onChange: (chosen: Set<string>) => void;
  /** What is chosen here, for the reader of the screen. */
  label: string;
  /** The text of «all». */
  all: string;
  /** The text of several chosen. */
  count: (n: number) => string;
  /** A long list is narrowed by typing on the keys, with no field, as a list of the browser is
   * (customer's decisions of 20.11.2026): what is typed is shown above the list, Backspace
   * takes a letter back. The text names it for a phone, where a field is shown instead. */
  search?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const touch = typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;
  // The keys are heard while the list is open, wherever the pointer stands; closed, the list
  // forgets what was typed.
  useEffect(() => {
    if (!open) {
      setText("");
      return;
    }
    if (!search || touch) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      const mark = target instanceof HTMLInputElement && target.type === "checkbox";
      // Another field of the page keeps its own typing.
      if (!mark && (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
      if (event.key === "Backspace") setText((was) => was.slice(0, -1));
      // A space on a mark or a button presses it, as everywhere.
      else if (event.key === " ") {
        if (mark || target instanceof HTMLButtonElement) return;
        setText((was) => (was ? `${was} ` : was));
      } else if (event.key.length === 1) setText((was) => was + event.key);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, search, touch]);
  useDismiss(open, close, root);
  const only = chosen.size === 1 ? options.find((o) => chosen.has(o.id))?.name : undefined;
  const toggle = (id: string) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    // Every one marked is the same as all.
    onChange(next.size === options.length ? new Set() : next);
  };
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  // Every word typed must stand in the name; the marked ones stay in sight.
  const shown = words.length
    ? options.filter((o) => words.every((w) => o.name.toLowerCase().includes(w)))
    : options;
  return (
    <div className={search ? "multi-pick wide" : "multi-pick"} ref={root}>
      <button type="button" className="secondary multi-pick-button" aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
        {chosen.size === 0 ? all : (only ?? count(chosen.size))}
        <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div className="popup multi-pick-pop" role="dialog" aria-label={label}>
          {/* A phone has no keys to type on until a field is touched: there the field stays. */}
          {search && touch ? (
            <input
              className="multi-pick-search"
              value={text}
              placeholder={search}
              aria-label={search}
              onChange={(e) => setText(e.target.value)}
            />
          ) : null}
          {search && !touch && text ? <div className="multi-pick-typed">{text}</div> : null}
          <label className="check">
            <input type="checkbox" checked={chosen.size === 0} onChange={() => onChange(new Set())} /> {all}
          </label>
          {shown.map((o) => (
            <label key={o.id} className="check">
              <input type="checkbox" checked={chosen.has(o.id)} onChange={() => toggle(o.id)} /> {o.name}
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}
