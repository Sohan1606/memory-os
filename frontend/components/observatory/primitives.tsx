"use client";
/**
 * Shared Observatory primitives.
 *
 * State language is deliberately uniform: every unknown, unmeasured or
 * unconfigured value renders through `StateBadge`, so the UI can never imply
 * certainty the backend did not provide.
 */
import type { CSSProperties, ReactNode } from "react";

const UNKNOWN = new Set([
  "INSUFFICIENT EVIDENCE", "NOT CONFIGURED", "NOT CONNECTED",
  "NOT AVAILABLE", "UNAVAILABLE", "DEGRADED",
]);

export function tone(value: string): { fg: string; bg: string; border: string } {
  const v = value.toUpperCase();
  if (v === "ACTIVE" || v === "TRUSTED" || v === "RELIABLE" ||
      v === "WELL CALIBRATED") {
    return { fg: "var(--accent)", bg: "var(--accent-dim)", border: "var(--accent-line)" };
  }
  if (v === "REAL AGENT") {
    return { fg: "var(--accent)", bg: "var(--accent-dim)", border: "var(--accent-line)" };
  }
  if (v === "DEGRADED" || v === "MIXED" || v === "UNCERTAIN" ||
      v === "REASONABLE" || v === "CONTEXTUAL") {
    return { fg: "#e8c37a", bg: "rgba(232,195,122,0.10)", border: "rgba(232,195,122,0.32)" };
  }
  if (v === "CONTRADICTED" || v === "UNRELIABLE" || v === "POORLY CALIBRATED" ||
      v === "AT_RISK" || v === "AT RISK") {
    return { fg: "#e88a7a", bg: "rgba(232,138,122,0.10)", border: "rgba(232,138,122,0.32)" };
  }
  return { fg: "var(--muted)", bg: "rgba(244,241,234,0.04)", border: "var(--line)" };
}

export function StateBadge({ value, title }: { value: string; title?: string }) {
  const c = tone(value);
  const unknown = UNKNOWN.has(value.toUpperCase());
  return (
    <span
      title={title ?? (unknown ? "The system has no measured value for this." : undefined)}
      style={{
        display: "inline-block", padding: "0.22rem 0.55rem", borderRadius: 3,
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.09em",
        textTransform: "uppercase", color: c.fg, background: c.bg,
        border: `1px solid ${c.border}`, whiteSpace: "nowrap",
      }}
    >
      {value}
    </span>
  );
}

export function Panel({ title, hint, right, children, style }: {
  title: string; hint?: string; right?: ReactNode; children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <section style={{
      border: "1px solid var(--line)", background: "var(--graphite-900)",
      borderRadius: 6, padding: "1.25rem 1.35rem", minWidth: 0, ...style,
    }}>
      <header style={{
        display: "flex", alignItems: "baseline", justifyContent: "space-between",
        gap: "1rem", marginBottom: hint ? "0.35rem" : "0.9rem",
      }}>
        <h3 style={{
          fontFamily: "var(--mono)", fontSize: "0.68rem", letterSpacing: "0.14em",
          textTransform: "uppercase", color: "var(--silver)", margin: 0,
        }}>{title}</h3>
        {right}
      </header>
      {hint && <p style={{
        fontSize: "0.78rem", color: "var(--muted)", margin: "0 0 0.9rem",
        lineHeight: 1.5,
      }}>{hint}</p>}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p style={{
      fontFamily: "var(--mono)", fontSize: "0.7rem", color: "var(--muted)",
      letterSpacing: "0.06em", lineHeight: 1.7, margin: 0,
    }}>{children}</p>
  );
}

export function Row({ label, value, mono = true }: {
  label: string; value: ReactNode; mono?: boolean;
}) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      gap: "1rem", padding: "0.5rem 0", borderBottom: "1px solid var(--line)",
    }}>
      <span style={{ fontSize: "0.8rem", color: "var(--silver)" }}>{label}</span>
      <span style={{
        fontFamily: mono ? "var(--mono)" : "var(--sans)", fontSize: "0.74rem",
        color: "var(--warm)", textAlign: "right",
      }}>{value}</span>
    </div>
  );
}

/** Confidence rendered as a bar, never as false precision. */
export function Meter({ value, label }: { value: number | null; label?: string }) {
  if (value === null || Number.isNaN(value)) {
    return <StateBadge value="INSUFFICIENT EVIDENCE" />;
  }
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "0.6rem" }}>
      <span style={{
        width: 72, height: 3, background: "var(--graphite-600)",
        borderRadius: 2, overflow: "hidden",
      }}>
        <span style={{
          display: "block", width: `${pct}%`, height: "100%",
          background: "var(--accent)",
        }} />
      </span>
      <span style={{
        fontFamily: "var(--mono)", fontSize: "0.68rem", color: "var(--silver)",
      }}>{label ?? `${pct}%`}</span>
    </span>
  );
}
