"use client";
/**
 * Agent workspace: live conversation against the LangGraph agent, with thread
 * switching to demonstrate short-term isolation vs long-term recall, plus the
 * live memory network and system health.
 */
import { useCallback, useEffect, useState } from "react";

import Chat from "@/components/Chat";
import MemoryGraph from "@/components/MemoryGraph";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api } from "@/lib/api";

const DEFAULT_THREADS = ["thread-alpha", "thread-beta"];

export default function WorkspacePage() {
  const { health, stats, memories, resetDemo, refresh } = useMemoryStore();
  const [threads, setThreads] = useState<string[]>(DEFAULT_THREADS);
  const [threadId, setThreadId] = useState(DEFAULT_THREADS[0]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.conversations()
      .then((d) => {
        if (cancelled) return;
        const ids = d.map((c) => c.id);
        setThreads((prev) => Array.from(new Set([...prev, ...ids])));
      })
      .catch(() => { /* no conversations yet */ });
    return () => { cancelled = true; };
  }, []);

  const newThread = useCallback(() => {
    const id = `thread-${Date.now().toString(36)}`;
    setThreads((t) => [...t, id]);
    setThreadId(id);
  }, []);

  const doReset = async () => {
    setBusy(true);
    try { await resetDemo(); await refresh(); } finally { setBusy(false); }
  };

  return (
    <div style={{ padding: "calc(var(--nav-h, 5rem) + 2rem) var(--pad) 4rem",
                  maxWidth: "var(--maxw)", margin: "0 auto" }}>
      <p className="label label-accent">Agent workspace</p>
      <h1 className="headline" style={{ marginTop: "0.9rem", maxWidth: "18ch" }}>
        Talk to it. Then start a new thread and test it.
      </h1>
      <p className="body body-lg" style={{ marginTop: "1.2rem", maxWidth: "62ch" }}>
        Each thread has its own short-term conversation state, checkpointed by{" "}
        <span className="mono">thread_id</span>. Long-term memory is shared across every thread
        for the same user — tell the agent something here, switch threads, and ask about it.
      </p>

      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", margin: "2rem 0 1.5rem" }}>
        {threads.map((t) => (
          <button key={t} className="chip" data-active={t === threadId} onClick={() => setThreadId(t)}>
            {t}
          </button>
        ))}
        <button className="chip" onClick={newThread}>+ New thread</button>
        <button className="chip" onClick={() => void doReset()} disabled={busy}>
          {busy ? "Resetting…" : "Reset demo data"}
        </button>
      </div>

      <div className="workspace-grid" style={{ display: "grid", gap: "1.5rem" }}>
        <div className="panel" style={{ padding: "1.4rem", minHeight: 560, display: "flex" }}>
          <Chat key={threadId} threadId={threadId} />
        </div>

        <aside style={{ display: "grid", gap: "1.5rem", alignContent: "start" }}>
          <div className="panel" style={{ padding: "0.5rem" }}>
            <MemoryGraph height={300} compact />
          </div>

          <div className="panel" style={{ padding: "1.2rem 1.3rem" }}>
            <p className="label">System health</p>
            <dl style={{ margin: "1rem 0 0", display: "grid", gap: "0.6rem" }}>
              {[
                ["Memories", stats ? `${stats.total}` : "—"],
                ["Vectors", health ? `${health.vector.count}` : "—"],
                ["Retrieval", health ? health.vector.mode.toUpperCase() : "—"],
                ["Embeddings", health?.embeddings.dimension
                  ? `MiniLM-L6-v2 · ${health.embeddings.dimension}d` : "—"],
                ["Agent", health ? `${health.agent.framework} · ${health.agent.graph}` : "—"],
                ["Checkpointer", health ? health.agent.checkpointer : "—"],
                ["Provider", health
                  ? `${health.provider.name}${health.provider.tool_calling ? " · tool calling" : " · deterministic"}`
                  : "—"],
                ["Voice", health ? health.voice.mode : "—"],
                ["LangMem", health ? health.langmem.state.toLowerCase() : "—"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between",
                                      gap: "1rem", flexWrap: "wrap" }}>
                  <dt className="mono" style={{ color: "var(--muted)" }}>{k}</dt>
                  <dd className="mono" style={{ margin: 0, color: "var(--accent)", textAlign: "right" }}>{v}</dd>
                </div>
              ))}
            </dl>
            {health && !health.provider.tool_calling && (
              <p className="body" style={{ fontSize: "0.8125rem", marginTop: "1rem" }}>
                No tool-calling model is configured, so the agent runs its deterministic demo
                planner. Memory storage, retrieval and versioning are fully real; only the model’s
                free-form tool choice is unavailable.
              </p>
            )}
          </div>

          <div className="panel" style={{ padding: "1.2rem 1.3rem" }}>
            <p className="label">Recently stored</p>
            <ul style={{ listStyle: "none", padding: 0, margin: "0.9rem 0 0", display: "grid", gap: "0.5rem" }}>
              {memories.slice(0, 5).map((m) => (
                <li key={m.id} className="body" style={{ fontSize: "0.8125rem" }}>
                  <span className="mono" style={{ color: "var(--accent)" }}>
                    {m.category.replace(/_/g, " ")}
                  </span>{" "}
                  {m.content}
                </li>
              ))}
              {memories.length === 0 && <li className="body">Nothing stored yet.</li>}
            </ul>
          </div>
        </aside>
      </div>

      <style>{`
        @media (min-width: 1080px) {
          .workspace-grid { grid-template-columns: minmax(0, 1.45fr) minmax(300px, 0.55fr); }
        }
      `}</style>
    </div>
  );
}
