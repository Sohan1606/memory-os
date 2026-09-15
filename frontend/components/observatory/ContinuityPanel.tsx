"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ContinuityItem, IntentTransition } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

/**
 * V8.2 §9 / §10 — open threads and how intent has evolved.
 *
 * Every intent transition states WHY it changed and carries its uncertainty,
 * so a shift in understanding is inspectable rather than silent.
 */
export default function ContinuityPanel({ refreshKey = 0 }:
  { refreshKey?: number }) {
  const [items, setItems] = useState<ContinuityItem[] | null>(null);
  const [transitions, setTransitions] = useState<IntentTransition[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([
        api.continuity(), api.intentTransitions(),
      ]);
      setItems(c.items);
      setTransitions(t.transitions);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const close = async (id: string) => {
    try {
      await api.closeContinuityItem(id, "Closed from the Observatory");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not close");
    }
  };

  if (error) return <Panel title="Open threads"><Empty>{error}</Empty></Panel>;
  if (!items || !transitions) {
    return <Panel title="Open threads"><Empty>Loading…</Empty></Panel>;
  }

  return (
    <Panel
      title="Open threads"
      hint="Things worth returning to, and how the system's understanding of
            your goal has shifted."
      right={<StateBadge value={`${items.length} OPEN`} />}
    >
      {items.length === 0 ? (
        <Empty>Nothing is currently being tracked as unfinished.</Empty>
      ) : items.slice(0, 6).map((item) => (
        <div key={item.id} style={{ padding: "0.6rem 0",
                                    borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        gap: "1rem", alignItems: "flex-start" }}>
            <p style={{ fontSize: "0.82rem", color: "var(--warm)", margin: 0,
                        lineHeight: 1.5 }}>{item.summary}</p>
            <button type="button" onClick={() => void close(item.id)}
                    style={buttonStyle}>Close</button>
          </div>
          <p style={{ fontFamily: "var(--mono)", fontSize: "0.66rem",
                      color: "var(--muted)", margin: "0.3rem 0 0" }}>
            {item.kind} · {item.reason.replace(/_/g, " ")}
          </p>
        </div>
      ))}

      <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                   letterSpacing: "0.12em", textTransform: "uppercase",
                   color: "var(--silver)", margin: "1.4rem 0 0.6rem" }}>
        How intent has evolved
      </h4>
      {transitions.length === 0 ? (
        <Empty>No intent change has been recorded yet.</Empty>
      ) : transitions.slice(0, 5).map((t) => (
        <div key={t.id} style={{ padding: "0.55rem 0",
                                 borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", gap: "1rem" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.68rem",
                           color: "var(--silver)" }}>
              {(t.from_status ?? "new")} → {t.to_status}
            </span>
            <StateBadge
              value={`${Math.round(t.uncertainty * 100)}% UNSURE`}
              title="How uncertain the system is about this reading."
            />
          </div>
          {t.changed_because && (
            <p style={{ fontSize: "0.76rem", color: "var(--muted)",
                        margin: "0.3rem 0 0", lineHeight: 1.5 }}>
              {t.changed_because}
            </p>
          )}
        </div>
      ))}
    </Panel>
  );
}

const buttonStyle = {
  fontFamily: "var(--mono)", fontSize: "0.6rem", letterSpacing: "0.08em",
  textTransform: "uppercase" as const, color: "var(--muted)",
  background: "transparent", border: "1px solid var(--line)",
  borderRadius: 3, padding: "0.22rem 0.5rem", cursor: "pointer",
  flexShrink: 0,
};
