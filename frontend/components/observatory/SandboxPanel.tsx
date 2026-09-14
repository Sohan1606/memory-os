"use client";
/** Counterfactual sandbox. Every result is explicitly marked as a simulation. */
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { SandboxResult } from "@/lib/types";
import { Empty, Meter, Panel } from "./primitives";

const EXAMPLES = [
  "What if I delay the migration by two weeks?",
  "What if I drop the lowest priority project?",
  "What if I focus only on the launch?",
];

export default function SandboxPanel() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(q: string) {
    if (q.trim().length < 3) return;
    setBusy(true);
    try {
      setResult(await api.simulate(q.trim()));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Simulation unavailable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Sandbox" hint="Explore a what-if. Your real data is never changed.">
      <form
        onSubmit={(e) => { e.preventDefault(); void run(question); }}
        style={{ display: "flex", gap: "0.5rem", marginBottom: "0.7rem" }}
      >
        <label htmlFor="sandbox-q" className="sr-only">What if question</label>
        <input
          id="sandbox-q"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What if I…"
          style={{
            flex: 1, minWidth: 0, background: "var(--graphite-800)",
            border: "1px solid var(--line-strong)", borderRadius: 3,
            padding: "0.55rem 0.7rem", color: "var(--warm)",
            fontSize: "0.82rem", fontFamily: "var(--sans)",
          }}
        />
        <button type="submit" disabled={busy || question.trim().length < 3} style={{
          fontFamily: "var(--mono)", fontSize: "0.64rem", letterSpacing: "0.1em",
          textTransform: "uppercase", padding: "0.55rem 0.9rem", borderRadius: 3,
          border: "1px solid var(--accent-line)", background: "var(--accent-dim)",
          color: "var(--accent)",
          cursor: busy || question.trim().length < 3 ? "not-allowed" : "pointer",
        }}>{busy ? "Running" : "Run"}</button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem",
                    marginBottom: "0.9rem" }}>
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button"
            onClick={() => { setQuestion(ex); void run(ex); }}
            style={{
              fontSize: "0.7rem", color: "var(--silver)", background: "transparent",
              border: "1px solid var(--line)", borderRadius: 3,
              padding: "0.3rem 0.5rem", cursor: "pointer",
            }}>{ex}</button>
        ))}
      </div>

      {error && <Empty>{error}</Empty>}
      {!error && !result && <Empty>No simulation run yet.</Empty>}

      {result && (
        <div style={{
          border: "1px dashed var(--accent-line)", borderRadius: 4,
          padding: "0.9rem 1rem", background: "rgba(110,231,215,0.04)",
        }}>
          <p style={{
            fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
            color: "var(--accent)", margin: "0 0 0.7rem",
          }}>{result.banner}</p>
          <p style={{ fontSize: "0.86rem", color: "var(--warm)", margin: "0 0 0.8rem" }}>
            {result.question}
          </p>
          <Meter value={result.confidence} label={`confidence ${Math.round(result.confidence * 100)}%`} />

          {([["Assumptions", result.assumptions],
             ["What changes", result.changed_variables],
             ["Projected effects", result.projected_effects],
             ["Risks", result.risks]] as const).map(([title, items]) => (
            items.length > 0 && (
              <div key={title} style={{ marginTop: "0.9rem" }}>
                <h4 style={{
                  fontFamily: "var(--mono)", fontSize: "0.6rem",
                  letterSpacing: "0.12em", textTransform: "uppercase",
                  color: "var(--silver)", margin: "0 0 0.35rem",
                }}>{title}</h4>
                <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                  {items.map((it) => (
                    <li key={it} style={{
                      fontSize: "0.78rem", color: "var(--silver)",
                      lineHeight: 1.6, marginBottom: "0.2rem",
                    }}>{it}</li>
                  ))}
                </ul>
              </div>
            )
          ))}
          <p style={{ fontSize: "0.72rem", color: "var(--muted)",
                      margin: "0.9rem 0 0", fontStyle: "italic" }}>
            {result.note}
          </p>
        </div>
      )}
    </Panel>
  );
}
