"use client";
/** What the system has learned about working with you, and how it rates itself. */
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { CognitionStatus } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

type SelfEval = Record<string, { question: string; answer: string; evidence: number }>;

export default function LearningPanel({ refreshKey }: { refreshKey: number }) {
  const [policies, setPolicies] = useState<CognitionStatus["policies"]>([]);
  const [evaluation, setEvaluation] = useState<SelfEval>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.learning()
      .then((d) => { setPolicies(d.policies); setEvaluation(d.self_evaluation); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : "Learning unavailable."));
  }, []);

  useEffect(load, [load, refreshKey]);

  async function consolidate() {
    setBusy(true);
    try { await api.consolidateLearning(); load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not run consolidation."); }
    finally { setBusy(false); }
  }

  return (
    <Panel
      title="Learning"
      hint="Adapted behaviour is only adopted after repeated evidence."
      right={
        <button type="button" onClick={() => void consolidate()} disabled={busy} style={{
          fontFamily: "var(--mono)", fontSize: "0.6rem", letterSpacing: "0.1em",
          textTransform: "uppercase", padding: "0.3rem 0.55rem", borderRadius: 3,
          border: "1px solid var(--line-strong)", background: "transparent",
          color: "var(--silver)", cursor: busy ? "wait" : "pointer",
        }}>{busy ? "Running" : "Review"}</button>
      }
    >
      {error && <Empty>{error}</Empty>}

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem",
      }}>How I work with you</h4>
      {policies.length === 0 ? (
        <Empty>No adapted behaviour yet — I haven&apos;t seen a repeated pattern.</Empty>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {policies.map((p) => (
            <li key={p.id} style={{ padding: "0.5rem 0", borderBottom: "1px solid var(--line)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.8rem" }}>
                <span style={{ fontSize: "0.82rem", color: "var(--warm)" }}>
                  {p.key.replace(/_/g, " ")}
                </span>
                <StateBadge value={p.value} />
              </div>
              {p.rationale && (
                <p style={{ margin: "0.2rem 0 0", fontSize: "0.72rem", color: "var(--muted)" }}>
                  {p.rationale}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--silver)", margin: "1.2rem 0 0.4rem",
      }}>How I rate myself</h4>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {Object.entries(evaluation).map(([key, item]) => (
          <li key={key} style={{
            display: "flex", justifyContent: "space-between", gap: "1rem",
            alignItems: "center", padding: "0.5rem 0",
            borderBottom: "1px solid var(--line)",
          }}>
            <span style={{ fontSize: "0.8rem", color: "var(--silver)" }}>{item.question}</span>
            <StateBadge value={item.answer} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}
