"use client";
/**
 * V8.4.2 Advanced Explanation Inspector.
 * Visualizes auditable explanation graphs, decisive factors, alternatives analysis,
 * causal chains, and historical truth vs current state.
 */
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ExplanationGraph } from "@/lib/types";
import { Empty, Meter, Panel, StateBadge } from "./primitives";

interface Props {
  subject: { kind: string; id: string } | null;
  onClose: () => void;
}

const INTENTS = [
  { id: "why", label: "Why" },
  { id: "why_not", label: "Why Not" },
  { id: "why_now", label: "Why Now" },
  { id: "what_changed", label: "What Changed" },
  { id: "what_evidence", label: "Evidence" },
  { id: "what_alternatives", label: "Alternatives" },
  { id: "what_caused_change", label: "Causality" },
] as const;

export default function WhyInspector({ subject, onClose }: Props) {
  const [intent, setIntent] = useState<string>("why");
  const [graph, setGraph] = useState<ExplanationGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!subject) return;
    let cancelled = false;
    setLoading(true);
    api.explainSubject(subject.kind, subject.id, intent)
      .then((data) => {
        if (cancelled) return;
        setGraph(data);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Explanation unavailable.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [subject, intent]);

  useEffect(() => {
    if (!subject) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [subject, onClose]);

  if (!subject) return null;

  const subj = graph?.subject;
  const correction = graph?.correction;
  const factors = graph?.decisive_factors || [];
  const alternatives = graph?.alternatives || [];
  const supporting = graph?.supporting_evidence || [];
  const counter = graph?.counter_evidence || [];
  const timeline = graph?.timeline || [];
  const causality = graph?.causality || { upstream: [], downstream: [] };

  return (
    <Panel
      title={`Why Inspector · ${subject.kind}`}
      right={
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {graph?.explanation_type && <StateBadge value={graph.explanation_type} />}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close explanation"
            style={{
              background: "none",
              border: "1px solid var(--line-strong)",
              borderRadius: 3,
              color: "var(--silver)",
              fontFamily: "var(--mono)",
              fontSize: "0.6rem",
              padding: "0.25rem 0.5rem",
              cursor: "pointer",
            }}
          >
            Close
          </button>
        </div>
      }
    >
      {/* Query Intent Filter Tabs */}
      <div
        style={{
          display: "flex",
          gap: "0.35rem",
          overflowX: "auto",
          paddingBottom: "0.5rem",
          marginBottom: "0.8rem",
          borderBottom: "1px solid var(--line)",
        }}
      >
        {INTENTS.map((item) => {
          const active = intent === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setIntent(item.id)}
              style={{
                background: active ? "var(--graphite-800)" : "transparent",
                border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
                borderRadius: 3,
                color: active ? "var(--accent)" : "var(--silver)",
                fontFamily: "var(--mono)",
                fontSize: "0.62rem",
                padding: "0.2rem 0.5rem",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      {error && <Empty>{error}</Empty>}
      {loading && !graph && <Empty>Assembling auditable explanation graph...</Empty>}

      {graph && (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {/* Summary Box */}
          <div
            style={{
              background: "var(--graphite-800)",
              border: "1px solid var(--line)",
              borderRadius: 4,
              padding: "0.75rem 0.9rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.4rem" }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: "0.65rem", color: "var(--silver)", textTransform: "uppercase" }}>
                Target: {subj?.label || `${subject.kind}:${subject.id}`}
              </span>
              {graph.confidence !== null && graph.confidence !== undefined && (
                <Meter value={graph.confidence} label={`Confidence ${(graph.confidence * 100).toFixed(0)}%`} />
              )}
            </div>
            <p style={{ fontSize: "0.85rem", color: "var(--warm)", lineHeight: 1.6, margin: 0 }}>
              {graph.summary}
            </p>
          </div>

          {/* Historical Truth vs Current State (if corrected / retired) */}
          {correction?.is_corrected && (
            <div
              style={{
                border: "1px solid rgba(232, 195, 122, 0.35)",
                background: "rgba(232, 195, 122, 0.05)",
                borderRadius: 4,
                padding: "0.7rem 0.85rem",
              }}
            >
              <span style={{ fontFamily: "var(--mono)", fontSize: "0.64rem", color: "#e8c37a", textTransform: "uppercase", display: "block", marginBottom: "0.3rem" }}>
                Historical Truth vs Current State · Correction Event
              </span>
              <p style={{ fontSize: "0.78rem", color: "var(--warm)", margin: "0 0 0.4rem", lineHeight: 1.5 }}>
                Reason: {correction.reason || "State transition recorded by user or policy."}
              </p>
              <div style={{ display: "flex", gap: "0.8rem", fontSize: "0.72rem", fontFamily: "var(--mono)" }}>
                {correction.previous_lifecycle && (
                  <span style={{ color: "var(--muted)" }}>
                    Then: <strong style={{ color: "var(--silver)" }}>{correction.previous_lifecycle}</strong>
                  </span>
                )}
                {correction.current_lifecycle && (
                  <span style={{ color: "var(--muted)" }}>
                    Now: <strong style={{ color: "var(--accent)" }}>{correction.current_lifecycle}</strong>
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Decisive Factors */}
          {factors.length > 0 && (
            <div>
              <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem" }}>
                Decisive Factors
              </h4>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "0.4rem" }}>
                {factors.map((f, i) => (
                  <div
                    key={i}
                    style={{
                      background: "var(--graphite-800)",
                      border: "1px solid var(--line)",
                      borderRadius: 3,
                      padding: "0.45rem 0.6rem",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.2rem" }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.65rem", color: "var(--silver)" }}>{f.name}</span>
                      <StateBadge value={String(f.value ?? "-")} />
                    </div>
                    <span style={{ fontSize: "0.72rem", color: "var(--muted)", lineHeight: 1.4, display: "block" }}>
                      {f.description}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Alternatives Analysis */}
          {alternatives.length > 0 && (
            <div>
              <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem" }}>
                Alternative Candidates &amp; Rejection Analysis
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                {alternatives.map((alt, i) => (
                  <div
                    key={i}
                    style={{
                      background: "var(--graphite-800)",
                      border: "1px solid var(--line)",
                      borderRadius: 3,
                      padding: "0.45rem 0.65rem",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: "0.6rem",
                    }}
                  >
                    <div>
                      <span style={{ fontSize: "0.76rem", color: "var(--warm)", display: "block" }}>{alt.label}</span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.65rem", color: "#e88a7a" }}>
                        Why Rejected: {alt.rejection_reason}
                      </span>
                    </div>
                    {alt.score !== undefined && alt.score !== null && (
                      <span style={{ fontFamily: "var(--mono)", fontSize: "0.65rem", color: "var(--muted)" }}>
                        score: {Number(alt.score).toFixed(2)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Supporting & Counter Evidence */}
          {(supporting.length > 0 || counter.length > 0) && (
            <div>
              <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem" }}>
                Evidence Drill-Down ({supporting.length} supporting, {counter.length} counter)
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                {supporting.map((ev, i) => (
                  <div
                    key={`sup_${i}`}
                    style={{
                      background: "var(--graphite-800)",
                      borderLeft: "2px solid var(--accent)",
                      border: "1px solid var(--line)",
                      borderRadius: 3,
                      padding: "0.4rem 0.6rem",
                    }}
                  >
                    <span style={{ fontSize: "0.75rem", color: "var(--warm)", display: "block" }}>{ev.content}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--muted)" }}>
                      {ev.source} · {ev.relation} · confidence {(ev.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
                {counter.map((ev, i) => (
                  <div
                    key={`cnt_${i}`}
                    style={{
                      background: "var(--graphite-800)",
                      borderLeft: "2px solid #e88a7a",
                      border: "1px solid var(--line)",
                      borderRadius: 3,
                      padding: "0.4rem 0.6rem",
                    }}
                  >
                    <span style={{ fontSize: "0.75rem", color: "var(--warm)", display: "block" }}>{ev.content}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "#e88a7a" }}>
                      Counterexample · {ev.source}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Causal Chain */}
          {(causality.upstream.length > 0 || causality.downstream.length > 0) && (
            <div>
              <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem" }}>
                Causal Graph
              </h4>
              <div style={{ display: "flex", gap: "0.8rem", fontSize: "0.74rem" }}>
                <div style={{ flex: 1, background: "var(--graphite-800)", padding: "0.5rem", borderRadius: 3, border: "1px solid var(--line)" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--silver)", display: "block", marginBottom: "0.2rem" }}>
                    Upstream Causes ({causality.upstream.length})
                  </span>
                  {causality.upstream.length === 0 ? (
                    <span style={{ color: "var(--muted)", fontSize: "0.7rem" }}>None</span>
                  ) : (
                    causality.upstream.map((u, i) => (
                      <div key={i} style={{ color: "var(--warm)", fontSize: "0.72rem" }}>
                        {u.cause_kind}:{u.cause_id} ({u.relation})
                      </div>
                    ))
                  )}
                </div>
                <div style={{ flex: 1, background: "var(--graphite-800)", padding: "0.5rem", borderRadius: 3, border: "1px solid var(--line)" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--silver)", display: "block", marginBottom: "0.2rem" }}>
                    Downstream Effects ({causality.downstream.length})
                  </span>
                  {causality.downstream.length === 0 ? (
                    <span style={{ color: "var(--muted)", fontSize: "0.7rem" }}>None</span>
                  ) : (
                    causality.downstream.map((d, i) => (
                      <div key={i} style={{ color: "var(--warm)", fontSize: "0.72rem" }}>
                        {d.effect_kind}:{d.effect_id} ({d.relation})
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Canonical Event Timeline */}
          {timeline.length > 0 && (
            <div>
              <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem" }}>
                Canonical Event Timeline ({timeline.length})
              </h4>
              <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {timeline.map((e) => (
                  <li
                    key={e.id}
                    style={{
                      padding: "0.35rem 0 0.35rem 0.8rem",
                      borderLeft: "1px solid var(--line-strong)",
                      marginLeft: "0.2rem",
                    }}
                  >
                    <span style={{ display: "block", fontSize: "0.76rem", color: "var(--warm)" }}>{e.summary}</span>
                    <span
                      style={{
                        display: "block",
                        fontFamily: "var(--mono)",
                        fontSize: "0.62rem",
                        color: "var(--muted)",
                        marginTop: "0.1rem",
                      }}
                    >
                      {new Date(e.created_at).toLocaleString()} · {e.type}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Provenance Footer */}
          <div
            style={{
              paddingTop: "0.4rem",
              borderTop: "1px solid var(--line)",
              fontFamily: "var(--mono)",
              fontSize: "0.62rem",
              color: "var(--muted)",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>Source: {graph.provenance.source} · v{graph.provenance.schema_version}</span>
            <span>Generated: {new Date(graph.provenance.generated_at).toLocaleTimeString()}</span>
          </div>
        </div>
      )}
    </Panel>
  );
}
