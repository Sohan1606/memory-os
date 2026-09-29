"use client";
/**
 * ZORQ unified state token (§12.3 deliverable 5 — design tokens for state).
 *
 * Semantically different states are visually distinguishable and NEVER
 * color-only: every token carries a glyph, a mono uppercase label and a
 * semantic tone. Unknown/unavailable states use hollow markers and dashed
 * borders so they can never read as solid success.
 *
 * Z-UI.1 refinement guarantees:
 *  - COMPLETED and VERIFIED are distinct: COMPLETED means execution finished
 *    with the outcome NOT established — it renders hollow amber with its own
 *    glyph and can never be mistaken for VERIFIED (solid green check).
 *  - LOCAL is a connection/execution-origin state, not an active operation —
 *    it renders steady. Only genuinely ongoing operations pulse.
 */
import type { ReactNode } from "react";

export type StateTone =
  | "ok" | "active" | "complete" | "warn" | "fail" | "unknown" | "off" | "offline";

interface ToneRule {
  tone: StateTone;
  exact?: string[];
  prefixes?: string[];
  glyph: string;
  hollow?: boolean;
  dashed?: boolean;
  pulse?: boolean;
}

const RULES: ToneRule[] = [
  // Evidence-established outcomes — solid affirmative
  { tone: "ok", exact: ["VERIFIED", "READY", "OK", "SYNCED", "SUPPORTED", "AVAILABLE_VERIFIED", "TRUSTED", "ACTIVE-SAFE", "LOCAL"], glyph: "✓" },
  // Genuinely ongoing operations — animated pulse only for real activity
  { tone: "active", exact: ["ACTIVE", "EXECUTING", "ANALYZING", "RETRIEVING", "PLANNING", "SYNCING", "EXECUTION", "VERIFICATION", "AUTHORIZATION"], prefixes: ["EXECUTING", "ANALYZ", "RETRIEV", "VERIFYING"], glyph: "◈", pulse: true },
  // Finished, outcome NOT established — hollow amber, never success-shaped
  { tone: "complete", exact: ["COMPLETED", "FINISHED", "DONE"], glyph: "◆", hollow: true },
  // Attention — solid warn
  { tone: "warn", exact: ["DEGRADED", "WARN", "PENDING", "PARTIAL", "PARTIALLY-IMPLEMENTED", "IMPLEMENTED", "CONFLICT", "CANCEL_REQUESTED", "HOLD"], glyph: "▲" },
  // Negative — solid fail
  { tone: "fail", exact: ["FAILED", "FAIL", "DENIED", "ERROR", "UNRELIABLE", "STOPPED", "CONTRADICTED"], glyph: "✕" },
  // Unknown — hollow, reserved for genuinely UNKNOWN-like states
  { tone: "unknown", exact: ["UNKNOWN", "UNVERIFIED", "SESSION-BOUND", "PROPOSED", "CONFLICTED", "UNMEASURED"], glyph: "?", hollow: true },
  // Offline family — hollow slate
  { tone: "offline", exact: ["OFFLINE"], glyph: "⌁", hollow: true },
  // Not-there family — hollow + dashed, the most visually muted
  { tone: "off", exact: ["UNAVAILABLE", "NOT-CONFIGURED", "NOT CONFIGURED", "NOT-IMPLEMENTED", "NOT IMPLEMENTED", "NOT CONNECTED", "NOT AVAILABLE", "DESIGNED", "NOT-MOCKED", "EMPTY", "NONE", "NOT_CONFIGURED"], glyph: "∅", hollow: true, dashed: true },
];

export function stateTone(value: string): { tone: StateTone; glyph: string; hollow: boolean; dashed: boolean; pulse: boolean } {
  const v = (value || "").toUpperCase();
  for (const rule of RULES) {
    if (rule.exact?.includes(v) || rule.prefixes?.some((p) => v.startsWith(p))) {
      return { tone: rule.tone, glyph: rule.glyph, hollow: !!rule.hollow, dashed: !!rule.dashed, pulse: !!rule.pulse };
    }
  }
  return { tone: "unknown", glyph: "·", hollow: true, dashed: false, pulse: false };
}

export default function StateToken({
  value, label, title, note, small, children,
}: { value: string; label?: ReactNode; title?: string; note?: string; small?: boolean; children?: ReactNode }) {
  const t = stateTone(value);
  return (
    <span
      className="z-state"
      data-tone={t.tone}
      data-hollow={t.hollow ? "true" : undefined}
      data-dashed={t.dashed ? "true" : undefined}
      data-pulse={t.pulse ? "true" : undefined}
      title={note ?? title ?? `${value}${t.dashed ? " — this capability does not exist yet; nothing is simulated" : ""}`}
      style={small ? { fontSize: "0.58rem", padding: "0.18rem 0.45rem" } : undefined}
    >
      <span className="z-dot" aria-hidden="true" />
      {label ?? children ?? value}
    </span>
  );
}
