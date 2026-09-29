"use client";
/**
 * Agent workspace conversation. Talks to the LangGraph agent through
 * POST /api/chat, shows which memories were recalled, and surfaces safe
 * operational activity (never private model reasoning).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import CognitiveSurface from "@/components/CognitiveSurface";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { useSpeech } from "@/hooks/useSpeech";
import { api, ApiError } from "@/lib/api";
import type { ChatActivity, CognitiveSurfaceState, LiveSurfaceState,
                    RetrievalResult, TurnCognition } from "@/lib/types";

interface Turn {
  role: "user" | "assistant";
  content: string;
  recalled?: RetrievalResult[];
  activity?: ChatActivity[];
  cognition?: TurnCognition | null;
  surface?: CognitiveSurfaceState | null;
}

const ACTIVITY_LABEL: Record<string, string> = {
  LOAD_CONTEXT: "Loading thread context",
  MEMORY_PRELOAD: "Searching long-term memory",
  MODEL_CALL: "Model responding",
  DEMO_PLANNER: "Deterministic demo planner",
  TOOL_SURFACE: "Tool surface narrowed for this turn",
  TOOL_DECISION: "Tool selected",
  SEARCH_MEMORY: "search_memory executed",
  SAVE_MEMORY: "save_memory executed",
  UPDATE_MEMORY: "update_memory executed",
  DELETE_MEMORY: "delete_memory executed",
  MEMORY_MANAGER: "Memory manager extracted a memory",
  CONSOLIDATE_MEMORY: "consolidate_memory executed",
  TOOL_RESULT: "Tool returned",
  TOOL_FAILED: "Tool failed",
  MODEL_REVISION: "Model revised with tool results",
  CONTEXT_BUILD: "Context assembled",
  PROVIDER_DEGRADED: "Provider degraded — deterministic reply",
  // v8.3.1: cognitive subsystems participating in the conversation.
  LIST_MISSIONS: "Mission registry consulted",
  GET_MISSION: "Mission detail read",
  CREATE_MISSION: "Mission created",
  UPDATE_MISSION: "Mission state changed",
  PAUSE_MISSION: "Mission paused",
  RESUME_MISSION: "Mission resumed",
  COMPLETE_MISSION: "Mission completed",
  ABANDON_MISSION: "Mission abandoned",
  ADD_MISSION_STEP: "Mission step recorded",
  COMPLETE_MISSION_STEP: "Mission step completed",
  MISSION_BLOCKERS: "Blockers checked",
  GET_WORLD_STATE: "World state read",
  GET_WORLD_CHANGES: "World changes read",
  GET_CURRENT_FOCUS: "Current focus assembled",
  GET_PREDICTIONS: "Predictions read",
  GET_ATTENTION_STATE: "Attention state read",
  GET_HISTORICAL_STATE: "History reconstructed",
  SIMULATE_SCENARIO: "Scenario simulated (nothing changed)",
  EXPLAIN: "Evidence gathered",
};

/**
 * The agent's activity list accumulates across a thread's turns (LangGraph
 * checkpoint state). Each turn begins with a LOAD_CONTEXT marker, so the
 * CURRENT turn's evidence — the only thing the intelligence rail may render —
 * is the segment from the LAST LOAD_CONTEXT onward. Without this slice,
 * events from earlier turns (e.g. a previous TOOL_DECISION) would falsely
 * evidence stages of the current turn.
 */
function currentTurnActivity(activity: ChatActivity[]): ChatActivity[] {
  const idx = activity.map((a) => a.type).lastIndexOf("LOAD_CONTEXT");
  return idx >= 0 ? activity.slice(idx) : activity;
}

export default function Chat({
  threadId, onTurn,
}: { threadId: string; onTurn?: (turn: {
  activity: ChatActivity[]; recalledCount: number; busy: boolean;
} | null) => void }) {
  const { refresh, health, select } = useMemoryStore();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputMode, setInputMode] = useState<"text" | "voice">("text");
  const [liveSurface, setLiveSurface] = useState<LiveSurfaceState | null>(null);
  const speech = useSpeech();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setTurns([]);
    onTurn?.(null);
    api.threadMessages(threadId)
      .then((d) => {
        if (cancelled) return;
        setTurns(d.messages.map((m) => ({
          role: m.role === "user" ? "user" : "assistant", content: m.content,
        })));
      })
      .catch(() => { /* new thread */ });
    return () => { cancelled = true; };
  }, [threadId, onTurn]);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [turns]);
  useEffect(() => {
    if (speech.transcript) setInput(speech.transcript);
  }, [speech.transcript]);

  const send = useCallback(async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setTurns((t) => [...t, { role: "user", content: message }]);
    setBusy(true);
    onTurn?.({ activity: [], recalledCount: 0, busy: true });
    let poll: ReturnType<typeof setInterval> | null = null;
    try {
      const mode = inputMode;
      const started = await api.startSurfaceTurn(threadId);
      setLiveSurface(started);
      poll = setInterval(() => {
        void api.surfaceTurn(started.conversation.correlation_id)
          .then(setLiveSurface)
          .catch(() => { /* final chat response remains authoritative */ });
      }, 180);
      const res = await api.chat(
        message, threadId, mode, started.conversation.correlation_id);
      setTurns((t) => [...t, {
        role: "assistant", content: res.answer,
        recalled: res.recalled, activity: res.activity,
        cognition: res.cognition, surface: res.surface,
      }]);
      onTurn?.({
        activity: currentTurnActivity(res.activity ?? []),
        recalledCount: (res.recalled ?? []).length,
        busy: false,
      });
      if (mode === "voice" && typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(res.answer));
      }
      setInputMode("text");
      speech.reset();
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The agent could not respond.");
    } finally {
      if (poll) clearInterval(poll);
      setLiveSurface(null);
      setBusy(false);
    }
  }, [input, busy, threadId, refresh, inputMode, speech, onTurn]);

  const provider = health?.provider;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div className="z-telemetry" style={{ marginBottom: "1rem" }} aria-label="Turn telemetry">
        <span className="z-telemetry-item" title={provider?.detail}>
          PROVIDER <b>{provider ? (provider.mode ?? provider.name.toUpperCase()) : "…"}</b>
        </span>
        {provider?.model && (
          <span className="z-telemetry-item">MODEL <b>{provider.model}</b></span>
        )}
        <span className="z-telemetry-item">THREAD <b>{threadId}</b></span>
        {health && (
          <span className="z-telemetry-item" title="Memory retrieval mode">
            RETRIEVAL <b>{health.vector.mode === "semantic" ? "SEMANTIC" : "KEYWORD"}</b>
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", display: "grid", gap: "1.1rem",
                    alignContent: "start", paddingRight: "0.25rem", minHeight: 220 }}>
        {turns.length === 0 && (
          <div className="panel" style={{ padding: "1.5rem" }}>
            <p className="label label-accent">ZORQ — start here</p>
            <p className="body" style={{ marginTop: "0.5rem" }}>
              Tell ZORQ something durable — “I prefer concise technical explanations” —
              then open a different thread and ask what it knows about you. Memory is
              governed by <span style={{ color: "var(--accent)" }}>MEMORY//OS</span>, the
              canonical memory subsystem.
            </p>
          </div>
        )}

        {turns.map((t, i) => (
          <article key={i} style={{
            justifySelf: t.role === "user" ? "end" : "start",
            maxWidth: "min(640px, 92%)",
          }}>
            <p className="label" style={{
              marginBottom: "0.4rem", fontFamily: "var(--mono)", letterSpacing: "0.2em",
              color: t.role === "user" ? "var(--z-ink-3)" : "var(--z-accent)",
            }}>
              {t.role === "user" ? "YOU" : "ZORQ"}
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

            {t.surface && (
              <div className="panel" style={{ marginTop: "0.7rem", padding: "0.9rem" }}>
                <CognitiveSurface surface={t.surface} />
              </div>
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

        {liveSurface && (
          <div className="panel" style={{ padding: "0.9rem", maxWidth: 640 }}>
            <CognitiveSurface surface={liveSurface} live />
          </div>
        )}
        {busy && !liveSurface && <p className="label label-accent">Starting cognitive turn…</p>}
        {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
        <div ref={endRef} />
      </div>

      {/*
        ZORQ command console — the primary interaction surface. Enter submits;
        the submit affordance is a compact system control, not a consumer CTA.
        Voice remains truthful: the browser SpeechRecognition fallback is
        labeled as such until the real voice runtime (Phase 3F) exists.
      */}
      <form onSubmit={(e) => { e.preventDefault(); void send(); }}
            className="z-console" style={{ marginTop: "1.2rem" }}>
        <label htmlFor="chat-input" className="z-console-label">ASK ZORQ</label>
        <input id="chat-input" className="z-console-input" value={input} disabled={busy}
               size={1}
               onChange={(e) => { setInput(e.target.value); setInputMode("text"); }}
               placeholder="Type or speak naturally — Enter to submit"
               aria-label="Message ZORQ" autoComplete="off" />
        {speech.supported ? (
          <button className="z-console-btn" type="button" disabled={busy}
                  aria-pressed={speech.listening}
                  data-listening={speech.listening ? "true" : undefined}
                  title="Voice input — browser SpeechRecognition fallback (the real voice runtime is Phase 3F)."
                  aria-label={speech.listening ? "Stop voice input" : "Start voice input"}
                  onClick={() => {
                    setInputMode("voice");
                    if (speech.listening) speech.stop(); else speech.start();
                  }}>
            {speech.listening ? "◉ MIC" : "◇ MIC"}
          </button>
        ) : (
          <span className="z-console-btn" title="Browser speech recognition is unavailable; text uses the same cognitive pipeline."
                aria-label="Microphone unavailable" style={{ cursor: "default" }}>
            MIC N/A
          </span>
        )}
        <button className="z-console-btn" type="submit" disabled={busy || !input.trim()}
                aria-label="Submit (Enter)" title="Submit — Enter">
          ⏎ ENTER
        </button>
        {speech.error && <p className="body" style={{ flexBasis: "100%", color: "var(--z-fail)", margin: 0 }}>{speech.error}</p>}
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
      <MaintenanceNotes maintenance={cognition.maintenance} />
    </details>
  );
}

/**
 * V10.1: canonical maintenance findings for this turn. Rendered ONLY when
 * real maintenance produced real findings or proposals — an irrelevant turn
 * shows nothing, and no stage or progress is ever invented client-side.
 */
function MaintenanceNotes({ maintenance }: { maintenance?: TurnCognition["maintenance"] }) {
  if (!maintenance || !maintenance.relevant) return null;
  const hasContent = maintenance.findings.length > 0 || maintenance.proposals.length > 0
    || maintenance.status === "MAINTENANCE_FAILED";
  if (!hasContent) return null;
  return (
    <div style={{ marginTop: "0.5rem", borderTop: "1px solid var(--line)", paddingTop: "0.5rem" }}>
      {maintenance.status === "MAINTENANCE_FAILED" && (
        <p className="body" style={{ margin: 0, fontSize: "0.78rem", color: "#e8a07a" }}>
          Personal-model maintenance could not complete for this turn.
        </p>
      )}
      {maintenance.findings.map((finding, index) => (
        <p className="body" key={finding.finding_id ?? index}
           style={{ margin: index ? "0.35rem 0 0" : 0, fontSize: "0.78rem", color: "var(--silver)" }}>
          {finding.summary}
        </p>
      ))}
      {maintenance.proposals.length > 0 && (
        <p className="body" style={{ margin: "0.4rem 0 0", fontSize: "0.76rem", color: "var(--muted)" }}>
          {maintenance.proposals.length} maintenance proposal{maintenance.proposals.length === 1 ? "" : "s"} await
          your explicit confirmation — nothing has been changed.
        </p>
      )}
    </div>
  );
}
