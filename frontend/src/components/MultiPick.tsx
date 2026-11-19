import { useCallback, useRef, useState } from "react";

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
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, root);
  const only = chosen.size === 1 ? options.find((o) => chosen.has(o.id))?.name : undefined;
  const toggle = (id: string) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    // Every one marked is the same as all.
    onChange(next.size === options.length ? new Set() : next);
  };
  return (
    <div className="multi-pick" ref={root}>
      <button type="button" className="secondary multi-pick-button" aria-label={label} aria-expanded={open} onClick={() => setOpen(!open)}>
        {chosen.size === 0 ? all : (only ?? count(chosen.size))}
        <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div className="popup multi-pick-pop" role="dialog" aria-label={label}>
          <label className="check">
            <input type="checkbox" checked={chosen.size === 0} onChange={() => onChange(new Set())} /> {all}
          </label>
          {options.map((o) => (
            <label key={o.id} className="check">
              <input type="checkbox" checked={chosen.has(o.id)} onChange={() => toggle(o.id)} /> {o.name}
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}
