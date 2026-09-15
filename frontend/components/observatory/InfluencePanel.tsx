"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MemoryInfluence } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

/**
 * V8.2 §8 — the memory → influence → outcome → reputation causal chain.
 *
 * The rule this panel makes visible: retrieval is not influence, and an
 * influence with no observed outcome is honestly shown as AWAITING EVIDENCE
 * rather than counted as a success.
 */
export default function InfluencePanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [items, setItems] = useState<MemoryInfluence[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.influences();
      setItems(next.influences);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  /**
   * Recording an outcome requires evidence. We pass the user's own words as
   * the evidence string; with none, the backend downgrades the verdict to
   * INSUFFICIENT EVIDENCE and reputation does not move.
   */
  const record = async (id: string, verdict: "SUPPORTED" | "CONTRADICTED") => {
    const detail = window.prompt(
      verdict === "SUPPORTED"
        ? "What happened that confirms this memory was right?"
        : "What happened that shows this memory was wrong?");
    if (!detail || !detail.trim()) return;
    setBusy(id);
    try {
      await api.recordInfluenceOutcome(id, {
        verdict, detail, evidence: [detail],
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record outcome");
    } finally {
      setBusy(null);
    }
  };

  if (error) return <Panel title="Memory influence"><Empty>{error}</Empty></Panel>;
  if (!items) return <Panel title="Memory influence"><Empty>Loading…</Empty></Panel>;

  const pending = items.filter((i) => !i.outcome_verdict).length;

  return (
    <Panel
      title="Memory influence"
      hint="Only memories that materially shaped a decision appear here.
            Retrieval alone is not influence, and reputation moves only on
            observed evidence."
      right={<StateBadge value={`${pending} AWAITING EVIDENCE`} />}
    >
      {items.length === 0 ? (
        <Empty>
          No memory has been recorded as influencing a decision yet.
        </Empty>
      ) : items.slice(0, 8).map((item) => (
        <div key={item.id} style={{ padding: "0.7rem 0",
                                    borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        gap: "1rem", alignItems: "flex-start" }}>
            <p style={{ fontSize: "0.8rem", color: "var(--warm)", margin: 0,
                        lineHeight: 1.5 }}>
              {item.how}
            </p>
            <StateBadge
              value={item.outcome_verdict ?? "AWAITING EVIDENCE"}
              title={item.reputation_effect
                ? `Reputation effect: ${item.reputation_effect}`
                : "No outcome has been observed yet."}
            />
          </div>
          <p style={{ fontFamily: "var(--mono)", fontSize: "0.66rem",
                      color: "var(--muted)", margin: "0.3rem 0 0" }}>
            shaped {item.influenced_kind} · {item.created_at}
          </p>

          {!item.outcome_verdict && (
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.55rem" }}>
              <button
                type="button"
                disabled={busy === item.id}
                onClick={() => void record(item.id, "SUPPORTED")}
                style={buttonStyle}
              >
                It was right
              </button>
              <button
                type="button"
                disabled={busy === item.id}
                onClick={() => void record(item.id, "CONTRADICTED")}
                style={buttonStyle}
              >
                It was wrong
              </button>
            </div>
          )}
        </div>
      ))}
    </Panel>
  );
}

const buttonStyle = {
  fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.08em",
  textTransform: "uppercase" as const, color: "var(--silver)",
  background: "transparent", border: "1px solid var(--line)",
  borderRadius: 3, padding: "0.3rem 0.6rem", cursor: "pointer",
};
