"use client";
/**
 * Retrieval scene. Runs a real backend search and animates the pipeline while
 * the request is in flight. Every score and reason shown comes from the server.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import MemoryGraph from "@/components/MemoryGraph";
import { api, ApiError } from "@/lib/api";
import type { SearchResponse } from "@/lib/types";

const STAGES = ["QUERY", "EMBEDDING", "VECTOR SEARCH", "RANKING", "CONTEXT", "RESPONSE"];
const SUGGESTIONS = [
  "What do you remember about my projects?",
  "How should you explain things to me?",
  "What technology areas interest me?",
];

export default function RetrievalDemo() {
  const [query, setQuery] = useState(SUGGESTIONS[0]);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [stage, setStage] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<string>("semantic");
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  useEffect(() => clearTimers, []);

  const run = useCallback(async (q: string) => {
    const trimmed = q.trim();
    clearTimers();
    setResult(null);
    setError(null);
    if (!trimmed) { setError("Enter a question to search your memory."); setStage(-1); return; }

    setBusy(true);
    setStage(0);
    STAGES.forEach((_, i) => {
      if (i === 0) return;
      timers.current.push(setTimeout(() => setStage(i), i * 190));
    });

    try {
      const res = await api.search(trimmed, 5);
      setResult(res);
      setMode(res.mode);
      setStage(STAGES.length - 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Retrieval failed.");
      setStage(-1);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void run(SUGGESTIONS[0]); }, [run]);

  const highlightIds = result?.results.map((r) => r.memory.id) ?? [];

  return (
    <div style={{ display: "grid", gap: "2rem" }}>
      <form onSubmit={(e) => { e.preventDefault(); void run(query); }}
            style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <label htmlFor="retrieval-q" className="sr-only">Ask your memory</label>
        <input id="retrieval-q" className="field" value={query}
               onChange={(e) => setQuery(e.target.value)}
               placeholder="Ask what the system remembers…"
               style={{ flex: "1 1 280px" }} />
        <button className="btn btn-primary" type="submit" disabled={busy} data-cursor="cta">
          {busy ? "Retrieving…" : "Retrieve"}
        </button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" data-active={query === s}
                  onClick={() => { setQuery(s); void run(s); }}>
            {s}
          </button>
        ))}
      </div>

      {/* pipeline */}
      <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex",
                   flexWrap: "wrap", gap: "0.4rem" }}>
        {STAGES.map((s, i) => {
          const done = stage >= i;
          return (
            <li key={s} className="mono" style={{
              padding: "0.4rem 0.7rem",
              border: `1px solid ${done ? "var(--accent-line)" : "var(--line)"}`,
              color: done ? "var(--accent)" : "var(--muted)",
              background: done ? "var(--accent-dim)" : "transparent",
              transition: "all .4s var(--ease)", fontSize: "0.5625rem",
              letterSpacing: "0.14em",
            }}>
              {String(i + 1).padStart(2, "0")} {s}
            </li>
          );
        })}
        <li className="mono" style={{ padding: "0.4rem 0.7rem", color: "var(--muted)",
                                      fontSize: "0.5625rem", letterSpacing: "0.14em" }}>
          MODE: {mode.toUpperCase()}
        </li>
      </ol>

      {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}

      <div className="retrieval-split" style={{ display: "grid", gap: "2rem" }}>
        <div className="panel" style={{ padding: "0.5rem" }}>
          <MemoryGraph highlight={{ ids: highlightIds }} height={420} compact />
        </div>

        <div>
          {result?.state === "NO_STRONG_MATCH" && (
            <div className="panel" style={{ padding: "1.5rem" }}>
              <p className="label label-accent">No strong match</p>
              <p className="body" style={{ marginTop: "0.6rem" }}>
                Nothing in memory is strongly relevant to that query. The system reports this
                rather than inventing a match.
              </p>
            </div>
          )}

          {result && result.results.length > 0 && (
            <>
              <p className="label" style={{ marginBottom: "0.8rem" }}>
                {result.results.length} relevant {result.results.length === 1 ? "memory" : "memories"}
                {result.state === "WEAK" && " · weak match"}
              </p>
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.65rem" }}>
                {result.results.map((r) => (
                  <li key={r.memory.id} className="panel" style={{ padding: "1rem 1.1rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between",
                                  gap: "1rem", alignItems: "baseline" }}>
                      <span className="label label-accent">{r.memory.category.replace(/_/g, " ")}</span>
                      <span className="mono" style={{
                        color: r.strength === "strong" ? "var(--accent)" : "var(--silver)",
                      }}>
                        {Math.round(r.score * 100)}%
                      </span>
                    </div>
                    <p className="body" style={{ color: "var(--warm)", margin: "0.5rem 0 0.6rem" }}>
                      {r.memory.content}
                    </p>
                    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex",
                                 flexWrap: "wrap", gap: "0.3rem" }}>
                      {r.reasons.map((why) => (
                        <li key={why} className="mono" style={{
                          fontSize: "0.5625rem", letterSpacing: "0.1em", color: "var(--muted)",
                          border: "1px solid var(--line)", padding: "0.2rem 0.45rem",
                        }}>
                          {why}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          )}

          {result && result.path.length > 0 && (
            <div style={{ marginTop: "1.5rem" }}>
              <p className="label">Retrieval path</p>
              <ol style={{ listStyle: "none", padding: 0, margin: "0.8rem 0 0" }}>
                {result.path.map((step, i) => (
                  <li key={`${step.kind}-${i}`} style={{
                    borderLeft: "1px solid var(--accent-line)", paddingLeft: "0.9rem",
                    paddingBottom: "0.75rem",
                  }}>
                    <p className="label label-accent">{step.kind}</p>
                    <p className="body" style={{ fontSize: "0.8125rem", margin: "0.2rem 0 0" }}>
                      {step.label}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      </div>

      <style>{`
        @media (min-width: 1000px) {
          .retrieval-split { grid-template-columns: 1.1fr 0.9fr; align-items: start; }
        }
      `}</style>
    </div>
  );
}
