"use client";
/**
 * Desktop-only custom cursor. Disabled on touch devices and under
 * prefers-reduced-motion, and it never blocks text selection or keyboard use.
 * States are declared with data-cursor="view|drag|node|cta".
 */
import { useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useReducedMotion } from "@/hooks/useReducedMotion";

type State = "default" | "interactive" | "view" | "drag" | "node" | "cta";

export default function CustomCursor() {
  const dot = useRef<HTMLDivElement>(null);
  const ring = useRef<HTMLDivElement>(null);
  const raf = useRef<number | null>(null);
  const pos = useRef({ x: -100, y: -100, rx: -100, ry: -100 });
  const [state, setState] = useState<State>("default");
  const [visible, setVisible] = useState(false);

  const fine = useMediaQuery("(pointer: fine)");
  const reduced = useReducedMotion();
  const enabled = fine && !reduced;

  useEffect(() => {
    if (!enabled) {
      document.body.classList.remove("cursor-active");
      return;
    }
    document.body.classList.add("cursor-active");

    const onMove = (e: PointerEvent) => {
      pos.current.x = e.clientX;
      pos.current.y = e.clientY;
      setVisible(true);

      const el = (e.target as HTMLElement | null)?.closest?.(
        "[data-cursor],a,button,input,textarea,select,[role='button']");
      if (!el) { setState("default"); return; }
      const explicit = el.getAttribute("data-cursor") as State | null;
      if (explicit) { setState(explicit); return; }
      const tag = el.tagName.toLowerCase();
      setState(tag === "input" || tag === "textarea" ? "default" : "interactive");
    };

    const onLeave = () => setVisible(false);

    const loop = () => {
      pos.current.rx += (pos.current.x - pos.current.rx) * 0.16;
      pos.current.ry += (pos.current.y - pos.current.ry) * 0.16;
      if (dot.current) {
        dot.current.style.transform =
          `translate3d(${pos.current.x}px, ${pos.current.y}px, 0) translate(-50%,-50%)`;
      }
      if (ring.current) {
        ring.current.style.transform =
          `translate3d(${pos.current.rx}px, ${pos.current.ry}px, 0) translate(-50%,-50%)`;
      }
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    return () => {
      document.body.classList.remove("cursor-active");
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, [enabled]);

  if (!enabled) return null;

  const label = state === "view" ? "VIEW" : state === "drag" ? "DRAG" : "";
  const size = state === "default" ? 26
    : state === "node" ? 40
    : state === "view" || state === "drag" ? 62
    : 46;

  return (
    <div aria-hidden="true" style={{ opacity: visible ? 1 : 0, transition: "opacity .3s" }}>
      <div ref={dot} style={{
        position: "fixed", top: 0, left: 0, zIndex: 9999, pointerEvents: "none",
        width: 4, height: 4, borderRadius: "50%",
        background: state === "cta" ? "var(--accent)" : "var(--warm)",
      }} />
      <div ref={ring} style={{
        position: "fixed", top: 0, left: 0, zIndex: 9999, pointerEvents: "none",
        width: size, height: size, borderRadius: "50%",
        border: `1px solid ${state === "default" ? "rgba(244,241,234,0.3)" : "var(--accent-line)"}`,
        background: state === "node" || state === "cta" ? "rgba(110,231,215,0.08)" : "transparent",
        display: "flex", alignItems: "center", justifyContent: "center",
        transition: "width .35s var(--ease), height .35s var(--ease), border-color .35s, background .35s",
      }}>
        {label && (
          <span className="mono" style={{ fontSize: "0.5rem", letterSpacing: "0.14em", color: "var(--accent)" }}>
            {label}
          </span>
        )}
      </div>
    </div>
  );
}
