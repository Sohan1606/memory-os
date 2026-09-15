"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BackgroundStatus } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

/**
 * V8.3 §13–§14 — background cognition.
 *
 * Deliberately unglamorous. Cycles that found nothing are shown as having
 * found nothing, because the alternative — dressing up empty work as activity
 * — is exactly the kind of fake liveliness this project rejects.
 *
 * Running a cycle is manual here: the system does not spin work off a page
 * refresh, and the rate limit is shown so that bound is visible.
 */
export default function BackgroundPanel({ refreshKey = 0 }:
  { refreshKey?: number }) {
  const [status, setStatus] = useState<BackgroundStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.background());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return <Panel title="Background cognition"><Empty>{error}</Empty></Panel>;
  }
  if (!status) {
    return <Panel title="Background cognition"><Empty>Loading…</Empty></Panel>;
  }

  const paused = status.state !== "enabled";

  return (
    <Panel
      title="Background cognition"
      hint="Bounded, cancellable upkeep. A cycle that finds nothing is recorded
            as finding nothing."
      right={<StateBadge value={status.state.toUpperCase()} />}
    >
      <p style={{ fontSize: "0.78rem", color: "var(--muted)",
                  margin: "0 0 0.7rem", lineHeight: 1.55 }}>
        {status.detail}
      </p>

      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap",
                    margin: "0 0 0.9rem" }}>
        <button type="button" disabled={busy} style={buttonStyle}
                onClick={() => void act(() =>
                  api.setBackgroundState(paused ? "enabled" : "paused"))}>
          {paused ? "Enable" : "Pause"}
        </button>
        <button type="button" disabled={busy || paused} style={buttonStyle}
                onClick={() => void act(() => api.runBackgroundCycle())}>
          Run one cycle
        </button>
      </div>

      <p style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                  color: "var(--muted)", margin: "0 0 0.8rem" }}>
        Limits: {status.max_tasks_per_cycle} task(s) per cycle ·
        {" "}{status.min_interval_s}s minimum interval
      </p>

      {status.recent_cycles.length === 0 ? (
        <Empty>No cycles have run yet.</Empty>
      ) : status.recent_cycles.slice(0, 6).map((cycle) => (
        <div key={cycle.id} style={{ padding: "0.55rem 0",
                                     borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        gap: "1rem", alignItems: "center" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.66rem",
                           color: "var(--silver)" }}>
              {cycle.trigger} · {cycle.state}
            </span>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                           color: "var(--muted)" }}>
              {cycle.duration_ms ?? 0}ms
            </span>
          </div>
          <p style={{ fontSize: "0.75rem", color: "var(--muted)",
                      margin: "0.3rem 0 0", lineHeight: 1.5 }}>
            {cycle.skipped_reason
              ? cycle.skipped_reason
              : cycle.findings.length === 0
                ? "Nothing needed attention."
                : `${cycle.findings.length} finding(s), ` +
                  `${cycle.changes_made} change(s).`}
          </p>
          {cycle.findings.slice(0, 3).map((finding, i) => (
            <p key={i} style={{ fontSize: "0.73rem", color: "var(--muted)",
                                margin: "0.25rem 0 0 0.6rem",
                                lineHeight: 1.45 }}>
              · {finding.summary}
            </p>
          ))}
        </div>
      ))}
    </Panel>
  );
}

const buttonStyle = {
  fontFamily: "var(--mono)", fontSize: "0.6rem", letterSpacing: "0.08em",
  textTransform: "uppercase" as const, color: "var(--muted)",
  background: "transparent", border: "1px solid var(--line)",
  borderRadius: 3, padding: "0.28rem 0.6rem", cursor: "pointer",
};
