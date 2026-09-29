"use client";
/**
 * ZORQ Workspace (Z-UI.1 refinement) — the primary surface.
 * The intelligence rail (signature pattern) renders the lifecycle with
 * backend evidence only; the conversation is centred and belongs to ZORQ;
 * the context rail carries runtime truth, proposed actions and the
 * authorization boundary. Progressive disclosure: nothing here exposes
 * chain-of-thought — only operational activity the backend reports.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import Chat from "@/components/Chat";
import IntelligenceRail, { type RailTurn } from "@/components/zorq/IntelligenceRail";
import {
  ErrorPanel, LoadingPanel, MetaBadge, PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api, zorqApi } from "@/lib/api";
import { providerPosture } from "@/lib/provider";

const DEFAULT_THREAD = "thread-main";

export default function WorkspacePage() {
  const { health } = useMemoryStore();
  const zorq = useFacade(zorqApi.status, 15_000);
  const [threads, setThreads] = useState<string[]>([DEFAULT_THREAD]);
  const [threadId, setThreadId] = useState(DEFAULT_THREAD);
  const [turn, setTurn] = useState<RailTurn | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.conversations().then((rows) => {
      if (!cancelled) setThreads((current) => Array.from(new Set([
        ...current, ...rows.map((row) => row.id),
      ])));
    }).catch(() => { /* a fresh workspace has no conversation records */ });
    return () => { cancelled = true; };
  }, []);

  const newConversation = useCallback(() => {
    const id = `thread-${Date.now().toString(36)}`;
    setThreads((current) => [id, ...current]);
    setThreadId(id);
  }, []);

  const onTurn = useCallback((t: RailTurn | null) => setTurn(t), []);

  const st = zorq.data?.available ? zorq.data : null;
  const posture = providerPosture(health);

  return (
    <div className="z-page">
      <PageHeader
        kicker="Primary · Workspace"
        title="Workspace"
        lede="Speak or type. The conversation follows the cognitive work the system actually performs — and any action ZORQ proposes appears with its authorization boundary."
        right={<MetaBadge muted={posture.state !== "REAL"} note={posture.note}>{posture.label}</MetaBadge>}
      />

      {/* ------------------------- signature pattern: the intelligence rail */}
      <section aria-label="Intelligence lifecycle" style={{ marginBottom: "1.1rem" }}>
        <IntelligenceRail turn={turn} />
      </section>

      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.9rem", alignItems: "center" }}>
        {threads.map((thread) => (
          <button key={thread} className="chip" data-active={thread === threadId}
                  onClick={() => setThreadId(thread)}>{thread}</button>
        ))}
        <button className="chip" onClick={newConversation}>+ New conversation</button>
        {health && (
          <span className="z-meta" data-muted="true" title={`Retrieval mode: ${health.vector.mode}. ${health.provider.detail}`}>
            {health.provider.name.toUpperCase()} · {health.vector.mode.toUpperCase()}
          </span>
        )}
      </div>

      <div className="z-workbench">
        <section className="panel" style={{ padding: "clamp(0.8rem, 2vw, 1.4rem)", minHeight: "min(72vh, 760px)", display: "flex", margin: 0 }}>
          <Chat key={threadId} threadId={threadId} onTurn={onTurn} />
        </section>

        <aside aria-label="Conversation context and runtime truth" style={{ display: "grid", gap: "1rem", alignContent: "start" }}>
          <ZPanel title="Runtime truth" hint="What is actually answering, and how memory is reached.">
            {!health && <LoadingPanel rows={3} />}
            {health && (
              <>
                <Readout k="Provider" muted>{health.provider.name.toUpperCase()}</Readout>
                <Readout k="Mode">
                  <MetaBadge muted={posture.state !== "REAL"} note={posture.note}>
                    {posture.state === "REAL" ? "REAL AGENT" : posture.state === "FALLBACK" ? "DETERMINISTIC FALLBACK" : "UNKNOWN"}
                  </MetaBadge>
                </Readout>
                <Readout k="Retrieval" muted>{health.vector.mode.toUpperCase()}</Readout>
                <Readout k="Memories" muted>{health.memory.count}</Readout>
              </>
            )}
            {st && <Readout k="Memory authority" muted>{st.memory_integration.canonical_authority}</Readout>}
          </ZPanel>

          <ZPanel title="Proposed actions" hint="Actions ZORQ proposes appear here — proposed, never executed.">
            {zorq.condition === "LOADING" && <LoadingPanel rows={2} />}
            {zorq.condition === "ERROR" && <ErrorPanel retry={zorq.refresh} />}
            {zorq.condition === "UNAVAILABLE" && zorq.reason && <UnavailablePanel reason={zorq.reason} />}
            {st && (
              <div className="z-condition" role="note">
                <span className="z-condition-label">None proposed</span>
                <p className="z-condition-note">
                  No action has been proposed in this conversation. A proposal will render
                  as <span style={{ fontFamily: "var(--mono)" }}>PROPOSED</span> — a
                  proposal, not an authorization and never an execution. The gate on the
                  rail (<Link href="/actions" className="z-chip">Actions</Link>) stays closed.
                </p>
              </div>
            )}
          </ZPanel>

          <ZPanel title="Authorization boundary" hint="What this conversation can never do on its own.">
            <div style={{ display: "grid", gap: "0.5rem" }}>
              {[
                "MODEL CONFIDENCE ≠ AUTHORIZATION",
                "INTENT ≠ AUTHORIZATION",
                "MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE",
                "PROACTIVE SUGGESTION ≠ AUTHORIZED ACTION",
              ].map((inv) => (
                <p key={inv} style={{
                  margin: 0, fontFamily: "var(--mono)", fontSize: "0.62rem",
                  letterSpacing: "0.1em", color: "var(--z-ink-3)",
                }}>{inv}</p>
              ))}
            </div>
          </ZPanel>

          <ZPanel title="This turn" hint="Evidence from the last turn, as reported by the backend.">
            {turn ? (
              <>
                <Readout k="Observed" muted>{turn.busy ? "in flight" : "yes"}</Readout>
                <Readout k="Activity events" muted>{turn.activity.length}</Readout>
                <Readout k="Memories recalled" muted>{turn.recalledCount}</Readout>
                <Readout k="Action evidence"><StateToken value={turn.activity.some((a) => a.type.includes("ACTION")) ? "EVIDENCED" : "NONE"} /></Readout>
              </>
            ) : (
              <div className="z-condition" role="note">
                <span className="z-condition-label">No turn yet</span>
                <p className="z-condition-note">Send a message and the rail fills with this turn{`'`}s real evidence.</p>
              </div>
            )}
          </ZPanel>
        </aside>
      </div>
    </div>
  );
}
