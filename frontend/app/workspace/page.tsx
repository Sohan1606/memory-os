"use client";
/** V9 conversation-first control plane. The backend-provided cognitive surface
 * is rendered inside each response; advanced subsystem inspection remains in
 * Observatory rather than competing with the conversation here. */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import Chat from "@/components/Chat";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api } from "@/lib/api";

const DEFAULT_THREAD = "thread-main";

export default function WorkspacePage() {
  const { health } = useMemoryStore();
  const [threads, setThreads] = useState<string[]>([DEFAULT_THREAD]);
  const [threadId, setThreadId] = useState(DEFAULT_THREAD);

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

  return (
    <main style={{ padding: "calc(var(--nav-h, 5rem) + 1.5rem) var(--pad) 3rem",
                   maxWidth: 1180, margin: "0 auto" }}>
      <header style={{ display: "flex", justifyContent: "space-between", gap: "1.5rem",
                       alignItems: "end", flexWrap: "wrap", marginBottom: "1.2rem" }}>
        <div>
          <p className="label label-accent">MEMORY//OS V9</p>
          <h1 className="headline" style={{ margin: "0.55rem 0 0", maxWidth: "18ch" }}>
            What are you thinking about?
          </h1>
          <p className="body" style={{ margin: "0.7rem 0 0", maxWidth: "58ch" }}>
            Speak or type naturally. The surface follows the cognitive work the system
            actually performs; uncertainty and unavailable capabilities stay visible.
          </p>
        </div>
        <Link href="/observatory" className="btn">Open Observatory</Link>
      </header>

      <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginBottom: "0.9rem" }}>
        {threads.map((thread) => (
          <button key={thread} className="chip" data-active={thread === threadId}
                  onClick={() => setThreadId(thread)}>{thread}</button>
        ))}
        <button className="chip" onClick={newConversation}>+ New conversation</button>
        <span className="chip" data-active={health?.status === "ok"} style={{ marginLeft: "auto" }}>
          {health ? `${health.provider.name.toUpperCase()} · ${health.vector.mode.toUpperCase()}` : "CONNECTING"}
        </span>
      </div>

      <section className="panel" style={{ padding: "clamp(0.8rem, 2vw, 1.4rem)",
                                          minHeight: "min(72vh, 760px)", display: "flex" }}>
        <Chat key={threadId} threadId={threadId} />
      </section>
    </main>
  );
}
