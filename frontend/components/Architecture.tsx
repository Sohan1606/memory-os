"use client";
/**
 * Conceptual system architecture. Layer states reflect the live /api/health
 * report, so what is shown as active is actually active.
 */
import { useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";

interface Layer { id: string; title: string; tech: string; body: string }

const LAYERS: Layer[] = [
  { id: "io", title: "Voice / Text", tech: "Web Speech API · optional faster-whisper",
    body: "Input arrives as speech or text. Both enter the identical agent pipeline — voice is not a separate system." },
  { id: "ui", title: "Interaction", tech: "Next.js App Router · React · TypeScript",
    body: "The interface renders entirely from backend state. Nothing on screen is hard-coded demo data." },
  { id: "agent", title: "Agent", tech: "LangGraph StateGraph · LangChain tools",
    body: "A compiled graph: load context → agent → tool loop → memory manager. When a tool-calling model is configured the model itself decides which memory tool to call." },
  { id: "extract", title: "Memory extraction", tech: "Deterministic policy engine · LangMem (optional)",
    body: "Durable, user-specific knowledge is separated from transient questions before anything is written. The policy engine always runs; LangMem takes over extraction only when it is installed and bound to a tool-calling model, and its real state is reported here." },
  { id: "index", title: "Memory index", tech: "ChromaDB · local MiniLM embeddings · SQLite",
    body: "Vectors live in Chroma; metadata, versions, relationships and audit events live in SQLite." },
  { id: "retrieval", title: "Retrieval", tech: "Hybrid semantic + keyword ranking",
    body: "Cosine similarity blended with category intent, importance, confidence and recency, behind an honest relevance threshold." },
  { id: "response", title: "Personalised response", tech: "Provider abstraction",
    body: "Retrieved memory becomes context. The active provider — demo, Ollama or OpenAI — is always reported truthfully." },
];

export default function Architecture() {
  const { health } = useMemoryStore();
  const [active, setActive] = useState<string>("agent");
  const current = LAYERS.find((l) => l.id === active) ?? LAYERS[0];

  const statusFor = (id: string): string | null => {
    if (!health) return null;
    switch (id) {
      case "io": return `VOICE: ${health.voice.mode.toUpperCase()}`;
      case "extract": return `LANGMEM: ${health.langmem.state}`;
      case "agent": return `${health.agent.framework.toUpperCase()} · CKPT ${health.agent.checkpointer.toUpperCase()}`;
      case "index": return `${health.vector.backend.toUpperCase()} · ${health.vector.count} VECTORS`;
      case "retrieval": return `MODE: ${health.vector.mode.toUpperCase()}`;
      case "response": return `PROVIDER: ${health.provider.name.toUpperCase()}`;
      default: return null;
    }
  };

  return (
    <div>
      <p className="label label-accent" style={{ marginBottom: "1.5rem" }}>
        Conceptual system architecture
      </p>

      <div className="arch-split" style={{ display: "grid", gap: "2rem" }}>
        <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.4rem" }}>
          {LAYERS.map((l, i) => {
            const isActive = l.id === active;
            const status = statusFor(l.id);
            return (
              <li key={l.id}>
                <button
                  onClick={() => setActive(l.id)}
                  aria-expanded={isActive}
                  className="panel"
                  style={{
                    width: "100%", textAlign: "left", padding: "1rem 1.2rem",
                    background: isActive ? "var(--accent-dim)" : "transparent",
                    borderColor: isActive ? "var(--accent-line)" : "var(--line)",
                    cursor: "pointer",
                    transform: isActive ? "translateX(10px)" : "none",
                    transition: "all .5s var(--ease)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between",
                                gap: "1rem", alignItems: "baseline", flexWrap: "wrap" }}>
                    <span style={{ display: "flex", gap: "0.9rem", alignItems: "baseline" }}>
                      <span className="mono" style={{ color: "var(--muted)" }}>
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="subhead" style={{
                        fontSize: "1.15rem", color: isActive ? "var(--accent)" : "var(--warm)",
                      }}>
                        {l.title}
                      </span>
                    </span>
                    {status && <span className="mono" style={{ color: "var(--muted)" }}>{status}</span>}
                  </div>
                </button>
              </li>
            );
          })}
        </ol>

        <div className="panel" style={{ padding: "1.6rem", alignSelf: "start", position: "sticky", top: "6rem" }}>
          <p className="label label-accent">{current.title}</p>
          <p className="mono" style={{ color: "var(--muted)", marginTop: "0.5rem" }}>{current.tech}</p>
          <p className="body" style={{ marginTop: "1rem" }}>{current.body}</p>
        </div>
      </div>

      <p className="body" style={{ marginTop: "2rem", fontSize: "0.8125rem", color: "var(--muted)", maxWidth: "70ch" }}>
        This diagram describes the architecture actually implemented in this repository. It is a
        local-first reference implementation, not a production deployment topology.
      </p>

      <style>{`
        @media (min-width: 1000px) { .arch-split { grid-template-columns: 1.15fr 0.85fr; } }
      `}</style>
    </div>
  );
}
