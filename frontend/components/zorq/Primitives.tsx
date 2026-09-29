"use client";
/**
 * ZORQ surface primitives (§12.3 deliverable 4 — component hierarchy).
 * Panels, readouts, page headers, first-class condition states
 * (loading / error / empty / unknown / unavailable / not-implemented),
 * the capability ladder chips and the action lifecycle stepper.
 */
import type { CSSProperties, ReactNode } from "react";

import StateToken from "./StateToken";

/* ------------------------------------------------------------- panels */

export function ZPanel({
  title, hint, right, children, style, wide,
}: {
  title: string; hint?: string; right?: ReactNode; children: ReactNode;
  style?: CSSProperties; wide?: boolean;
}) {
  return (
    <section className="z-panel" style={{ gridColumn: wide ? "1 / -1" : undefined, ...style }}>
      <div className="z-panel-head">
        <h3 className="z-panel-title">{title}</h3>
        {right}
      </div>
      {hint && <p className="z-panel-hint">{hint}</p>}
      {children}
    </section>
  );
}

export function Readout({
  k, children, muted,
}: { k: string; children: ReactNode; muted?: boolean }) {
  return (
    <div className="z-readout">
      <span className="z-readout-key">{k}</span>
      <span className="z-readout-val" data-muted={muted ? "true" : undefined}>{children}</span>
    </div>
  );
}

export function PageHeader({
  kicker, title, lede, right,
}: { kicker: ReactNode; title: ReactNode; lede?: ReactNode; right?: ReactNode }) {
  return (
    <div style={{ marginBottom: "2.1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "1.5rem", flexWrap: "wrap" }}>
        <div>
          <p className="z-page-kicker">{kicker}</p>
          <h1 className="z-page-title">{title}</h1>
          {lede && <p className="z-page-lede">{lede}</p>}
        </div>
        {right}
      </div>
    </div>
  );
}

/* -------------------------------------------------- condition states */

export function ConditionPanel({
  label, note, children,
}: { label: string; note?: string; children?: ReactNode }) {
  return (
    <div className="z-condition" role="note">
      <span className="z-condition-label">{label}</span>
      {note && <p className="z-condition-note">{note}</p>}
      {children}
    </div>
  );
}

export function LoadingPanel({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" aria-label={`${label} — loading`}>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.7rem" }}>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="z-skeleton" style={{ height: "0.85rem", width: `${88 - i * 18}%` }} />
        ))}
      </div>
    </div>
  );
}

export function ErrorPanel({ retry, detail }: { retry?: () => void; detail?: string }) {
  return (
    <ConditionPanel
      label="Error"
      note={detail ?? "The backend could not be reached. This is a real error, not a simulated state."}
    >
      {retry && (
        <button className="btn" style={{ padding: "0.5rem 1rem", fontSize: "0.62rem" }} onClick={retry}>
          Retry
        </button>
      )}
    </ConditionPanel>
  );
}

export function UnavailablePanel({ reason, retry }: { reason: string; retry?: () => void }) {
  return (
    <ConditionPanel
      label="Unavailable"
      note={`${reason} Nothing is simulated to fill this gap.`}
    >
      {retry && (
        <button className="btn" style={{ padding: "0.5rem 1rem", fontSize: "0.62rem" }} onClick={retry}>
          Retry
        </button>
      )}
    </ConditionPanel>
  );
}

export function NotImplementedPanel({ what, phase }: { what: string; phase: string }) {
  return (
    <ConditionPanel
      label="Not implemented"
      note={`${what} arrives in ${phase}. The interaction architecture is prepared; nothing is simulated as active.`}
    />
  );
}

/* ---------------------------------------------------------- metadata badge */

/**
 * Neutral badge for DESCRIPTIVE METADATA — mode, role, kind — never a system
 * state (Z-UI.1 refinement, MUST-FIX 4). CANONICAL, READ-ONLY, SEALED,
 * provider mode and similar descriptors render here; StateToken is reserved
 * for actual system states, and the unknown visual treatment is reserved for
 * genuinely UNKNOWN-like states.
 */
export function MetaBadge({
  children, note, muted,
}: { children: ReactNode; note?: string; muted?: boolean }) {
  return (
    <span className="z-meta" data-muted={muted ? "true" : undefined} title={note}>
      {children}
    </span>
  );
}

/* ------------------------------------------------- capability ladder */

export function LadderChips({
  visible, available, authorized, executable,
}: { visible: boolean; available: boolean; authorized: boolean | "SESSION-BOUND"; executable: boolean }) {
  const items: { level: string; on: boolean | "partial"; note?: string }[] = [
    { level: "VISIBLE", on: visible },
    { level: "AVAILABLE", on: available },
    { level: "AUTHORIZED", on: authorized === true ? true : authorized === "SESSION-BOUND" ? "partial" : false, note: authorized === "SESSION-BOUND" ? "Session-bound — cannot be assessed from this view" : undefined },
    { level: "EXECUTABLE", on: executable },
  ];
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }} aria-label="Capability ladder — visibility is not permission">
      {items.map((item) => (
        <span
          key={item.level}
          className="z-state"
          data-tone={item.on === true ? "ok" : item.on === "partial" ? "unknown" : "off"}
          data-hollow={item.on === true ? undefined : "true"}
          data-dashed={item.on === false ? "true" : undefined}
          title={item.note ?? (item.on === true ? "True" : "False — never rendered as enabled")}
        >
          <span className="z-dot" aria-hidden="true" />
          {item.level}
        </span>
      ))}
    </div>
  );
}

/* ----------------------------------------------- action lifecycle UI */

export const ACTION_LIFECYCLE = [
  "PROPOSED", "AUTHORIZATION", "SNAPSHOT", "LEASE",
  "EXECUTION", "VERIFICATION", "AUDIT",
] as const;

export function LifecycleStepper({
  phases, current,
}: { phases: readonly string[]; current?: string }) {
  return (
    <div className="z-steps" role="list" aria-label="Action lifecycle">
      {phases.map((phase, i) => (
        <span key={phase} style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }} role="listitem">
          <span className="z-step" data-idx={String(i + 1)} data-state={current === phase ? "current" : undefined}>
            {phase}
          </span>
          {i < phases.length - 1 && <span className="z-arrow" aria-hidden="true">→</span>}
        </span>
      ))}
    </div>
  );
}

/* ---------------------------------------------------- composed badge */

export function StateValue({ value, note }: { value: string; note?: string }) {
  return <StateToken value={value} title={note} />;
}
