/**
 * Provider truth (Z-UI.1 refinement, MUST-FIX 1).
 *
 * The backend contract (backend/app/providers/base.py) defines exactly two
 * provider modes: "REAL AGENT" and "DETERMINISTIC FALLBACK". This module is
 * the single authoritative mapping for every surface — no page may compare
 * the mode string against anything else.
 *
 *   REAL AGENT            only when mode === "REAL AGENT"
 *   DETERMINISTIC FALLBACK when mode === "DETERMINISTIC FALLBACK"
 *   UNKNOWN               when provider state cannot be obtained
 *
 * An unreachable provider is never defaulted to REAL or DEMO.
 */
import type { Health } from "./types";

export type ProviderPostureState = "REAL" | "FALLBACK" | "UNKNOWN";

export interface ProviderPosture {
  state: ProviderPostureState;
  /** Display label for badges. */
  label: string;
  /** Short note explaining the posture, for titles/tooltips. */
  note: string;
}

export const REAL_AGENT_MODE = "REAL AGENT";
export const DETERMINISTIC_FALLBACK_MODE = "DETERMINISTIC FALLBACK";

export function providerPosture(health: Health | null | undefined): ProviderPosture {
  if (!health || typeof health.provider?.mode !== "string") {
    return {
      state: "UNKNOWN",
      label: "PROVIDER · UNKNOWN",
      note: "Provider state could not be obtained from the backend. No assumption is made.",
    };
  }
  if (health.provider.mode === REAL_AGENT_MODE) {
    return {
      state: "REAL",
      label: "PROVIDER · REAL AGENT",
      note: health.provider.detail || "A real model is answering and may call tools.",
    };
  }
  if (health.provider.mode === DETERMINISTIC_FALLBACK_MODE) {
    return {
      state: "FALLBACK",
      label: "PROVIDER · DETERMINISTIC FALLBACK",
      note:
        health.provider.detail ||
        "No model is configured. The deterministic demo planner composes replies; nothing is simulated as real intelligence.",
    };
  }
  return {
    state: "UNKNOWN",
    label: "PROVIDER · UNKNOWN",
    note: `Unrecognized provider mode "${health.provider.mode}". No assumption is made.`,
  };
}
