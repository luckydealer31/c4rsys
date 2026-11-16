import { useEffect, useRef } from "react";

import bell from "./bell-ring.svg?raw";
import box from "./package-open.svg?raw";
import settings from "./settings-ratchet.svg?raw";
import wallet from "./wallet-fill.svg?raw";
import wrench from "./wrench-turn.svg?raw";

/** Animated icons from Gentleface (https://gentleface.com, MIT licensed), kept as downloaded:
 * native SVG animation, no script. The drawing rests on its first frame and plays once when the
 * pointer comes to the link or the button it stands in, or when that gets the focus (customer's
 * decision of 11.11.2026). People who ask for reduced motion get the still drawing. */
const DRAWINGS = { wallet, settings, bell, box, wrench } as const;
// The part of the loop worth showing once, in seconds — as the source of the icon advises.
const PLAY = { wallet: [0.18, 2.5], settings: [0.25, 2.1], bell: [0.2, 1.5], box: [0.25, 2.7], wrench: [0.25, 3.1] } as const;

export type MotionName = keyof typeof DRAWINGS;

export function MotionIcon({ name, size = 20 }: { name: MotionName; size?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const span = ref.current;
    const svg = span?.querySelector("svg");
    if (!span || !svg || typeof svg.pauseAnimations !== "function") return;
    svg.pauseAnimations();
    svg.setCurrentTime(0);
    const host: Element = span.closest("a, button") ?? span;
    const [from, to] = PLAY[name];
    let timer = 0;
    const play = () => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      window.clearTimeout(timer);
      svg.setCurrentTime(from);
      svg.unpauseAnimations();
      timer = window.setTimeout(() => {
        svg.pauseAnimations();
        svg.setCurrentTime(0);
      }, (to - from) * 1000);
    };
    host.addEventListener("mouseenter", play);
    host.addEventListener("focus", play);
    return () => {
      window.clearTimeout(timer);
      host.removeEventListener("mouseenter", play);
      host.removeEventListener("focus", play);
    };
  }, [name]);
  const markup = DRAWINGS[name].replace('width="16" height="16"', `class="icon" width="${size}" height="${size}" aria-hidden="true"`);
  return <span ref={ref} style={{ display: "inline-flex" }} dangerouslySetInnerHTML={{ __html: markup }} />;
}
