"use client";
/** Shared editorial section shell with a scroll-triggered reveal. */
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useReducedMotion } from "@/hooks/useReducedMotion";

interface Props {
  id?: string;
  index?: string;
  label?: string;
  title?: ReactNode;
  lede?: ReactNode;
  children?: ReactNode;
  wide?: boolean;
}

export default function Section({ id, index, label, title, lede, children, wide = false }: Props) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (reduced) { setShown(true); return; }
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setShown(true); io.disconnect(); } },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.05 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);

  return (
    <section
      ref={ref}
      id={id}
      className="section"
      data-shown={shown}
      style={{ ["--section-max" as string]: wide ? "1400px" : "1180px" }}
    >
      <div className="section-inner">
        {(index || label) && (
          <p className="label" style={{ display: "flex", gap: "1rem" }}>
            {index && <span style={{ color: "var(--accent)" }}>{index}</span>}
            {label && <span>{label}</span>}
          </p>
        )}
        {title && <h2 className="headline" style={{ marginTop: "1rem", maxWidth: "20ch" }}>{title}</h2>}
        {lede && <p className="body body-lg" style={{ marginTop: "1.4rem", maxWidth: "60ch" }}>{lede}</p>}
        {children && <div style={{ marginTop: title || lede ? "3.5rem" : "1.5rem" }}>{children}</div>}
      </div>
    </section>
  );
}
