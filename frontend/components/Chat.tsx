"use client";
/**
 * Agent workspace conversation. Talks to the LangGraph agent through
 * POST /api/chat, shows which memories were recalled, and surfaces safe
 * operational activity (never private model reasoning).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api, ApiError } from "@/lib/api";
import type { ChatActivity, RetrievalResult, TurnCognition } from "@/lib/types";

interface Turn {
  role: "user" | "assistant";
  content: string;
  recalled?: RetrievalResult[];
  activity?: ChatActivity[];
  cognition?: TurnCognition | null;
}

const ACTIVITY_LABEL: Record<string, string> = {
  LOAD_CONTEXT: "Loading thread context",
  MEMORY_PRELOAD: "Searching long-term memory",
  MODEL_CALL: "Model responding",
  DEMO_PLANNER: "Deterministic demo planner",
  TOOL_DECISION: "Tool selected",
  SEARCH_MEMORY: "search_memory executed",
  SAVE_MEMORY: "save_memory executed",
  UPDATE_MEMORY: "update_memory executed",
  DELETE_MEMORY: "delete_memory executed",
  MEMORY_MANAGER: "Memory manager extracted a memory",
};

export default function Chat({ threadId }: { threadId: string }) {
  const { refresh, health, select } = useMemoryStore();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setTurns([]);
    api.threadMessages(threadId)
      .then((d) => {
        if (cancelled) return;
        setTurns(d.messages.map((m) => ({
          role: m.role === "user" ? "user" : "assistant", content: m.content,
        })));
      })
      .catch(() => { /* new thread */ });
    return () => { cancelled = true; };
  }, [threadId]);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [turns]);

  const send = useCallback(async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setTurns((t) => [...t, { role: "user", content: message }]);
    setBusy(true);
    try {
      const res = await api.chat(message, threadId);
      setTurns((t) => [...t, {
        role: "assistant", content: res.answer,
        recalled: res.recalled, activity: res.activity,
        cognition: res.cognition,
      }]);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The agent could not respond.");
    } finally {
      setBusy(false);
    }
  }, [input, busy, threadId, refresh]);

  const provider = health?.provider;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "1rem" }}>
        <span className="chip" data-active={provider?.mode === "REAL AGENT"}
              title={provider?.detail}>
          {provider ? `MODE: ${provider.mode ?? provider.name.toUpperCase()}` : "MODE: …"}
        </span>
        {provider && (
          <span className="chip">PROVIDER: {provider.name.toUpperCase()}</span>
        )}
        {provider?.model && <span className="chip">{provider.model}</span>}
        <span className="chip">THREAD: {threadId}</span>
        {health && (
          <span className="chip">
            MEMORY: {health.vector.mode === "semantic" ? "CHROMA + LOCAL EMBEDDINGS" : "KEYWORD FALLBACK"}
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", display: "grid", gap: "1.1rem",
                    alignContent: "start", paddingRight: "0.25rem", minHeight: 220 }}>
        {turns.length === 0 && (
          <div className="panel" style={{ padding: "1.5rem" }}>
            <p className="label label-accent">Start here</p>
            <p className="body" style={{ marginTop: "0.5rem" }}>
              Tell the agent something durable — “I prefer concise technical explanations” —
              then open a different thread and ask what it knows about you.
            </p>
          </div>
        )}

        {turns.map((t, i) => (
          <article key={i} style={{
            justifySelf: t.role === "user" ? "end" : "start",
            maxWidth: "min(640px, 92%)",
          }}>
            <p className="label" style={{ marginBottom: "0.4rem" }}>
              {t.role === "user" ? "You" : "MEMORY//OS"}
            </p>
            <div className="panel" style={{
              padding: "0.9rem 1.1rem",
              borderColor: t.role === "user" ? "var(--line)" : "var(--accent-line)",
            }}>
              <p className="body" style={{ color: "var(--warm)", whiteSpace: "pre-wrap", margin: 0 }}>
                {t.content}
              </p>
            </div>

            {t.recalled && t.recalled.length > 0 && (
              <details style={{ marginTop: "0.55rem" }}>
                <summary className="label label-accent" style={{ cursor: "pointer" }}>
                  Memory used · {t.recalled.length}
                </summary>
                <ul style={{ listStyle: "none", padding: 0, margin: "0.6rem 0 0", display: "grid", gap: "0.4rem" }}>
                  {t.recalled.map((r) => (
                    <li key={r.memory.id}>
                      <button onClick={() => select(r.memory.id)} className="panel"
                              style={{ width: "100%", textAlign: "left", padding: "0.55rem 0.7rem",
                                       background: "transparent", cursor: "pointer" }}>
                        <span className="mono" style={{ color: "var(--accent)" }}>
                          {Math.round(r.score * 100)}% · {r.memory.category.replace(/_/g, " ")}
                        </span>
                        <span className="body" style={{ display: "block", fontSize: "0.8125rem" }}>
                          {r.memory.content}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {t.cognition && <Understanding cognition={t.cognition} />}

            {t.activity && t.activity.length > 0 && (
              <details style={{ marginTop: "0.4rem" }}>
                <summary className="label" style={{ cursor: "pointer" }}>Agent activity</summary>
                <ul style={{ listStyle: "none", padding: 0, margin: "0.5rem 0 0" }}>
                  {t.activity.map((a, k) => (
                    <li key={k} className="mono" style={{ color: "var(--muted)", padding: "0.15rem 0" }}>
                      → {ACTIVITY_LABEL[a.type] ?? a.type}
                      {typeof a.tool === "string" ? `: ${a.tool}` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))}

        {busy && <p className="label label-accent">Agent processing…</p>}
        {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
        <div ref={endRef} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); void send(); }}
            style={{ display: "flex", gap: "0.6rem", marginTop: "1.2rem", flexWrap: "wrap" }}>
        <label htmlFor="chat-input" className="sr-only">Message the agent</label>
        <input id="chat-input" className="field" value={input} disabled={busy}
               onChange={(e) => setInput(e.target.value)}
               placeholder="Tell the agent something worth remembering…"
               style={{ flex: "1 1 240px" }} />
        <button className="btn btn-primary" type="submit" disabled={busy || !input.trim()}
                data-cursor="cta">
          Send
        </button>
      </form>
    </div>
  );
}

/**
 * Silent intelligence: a short, human-readable account of what the system
 * understood this turn. Technical detail lives in the Observatory.
 */
function Understanding({ cognition }: { cognition: TurnCognition }) {
  const notes: string[] = [];
  if (cognition.need) notes.push(`Read this as a request for ${cognition.need.replace(/_/g, " ")}`);
  if (cognition.recalled > 0) notes.push(`recalled ${cognition.recalled} memory${cognition.recalled === 1 ? "" : "ies"}`);
  for (const e of cognition.world_entities) notes.push(`noted a ${e.kind}`);
  if (cognition.degraded) notes.push("memory search ran in a degraded mode");

  if (notes.length === 0) return null;

  return (
    <details style={{ marginTop: "0.4rem" }}>
      <summary className="label" style={{ cursor: "pointer" }}>Understanding</summary>
      <p className="body" style={{ margin: "0.5rem 0 0", fontSize: "0.82rem",
                                   color: "var(--silver)" }}>
        {notes.join(" · ")}.
      </p>
      {cognition.arbitration && (
        <p className="body" style={{ margin: "0.4rem 0 0", fontSize: "0.8rem",
                                     color: "var(--muted)" }}>
          Chose between competing memories: {cognition.arbitration.explanation}
          {cognition.arbitration.conflict ? " This was a close call." : ""}
        </p>
      )}
      {cognition.attention?.surface && (
        <p className="body" style={{ margin: "0.4rem 0 0", fontSize: "0.8rem",
                                     color: "var(--muted)" }}>
          Why now: {cognition.attention.why_now}
        </p>
      )}
    </details>
  );
}
