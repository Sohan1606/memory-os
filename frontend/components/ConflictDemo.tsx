"use client";
/**
 * Conflict / update demonstration.
 *
 * This performs a REAL mutation: it creates (or reuses) a dark-mode preference,
 * then submits a contradicting signal. The backend detects the conflict, updates
 * the same memory and increments its version. The UI renders the resolved object
 * returned by the server - there is no separate animation state that could drift
 * out of sync, and all timers are cancelled on restart/unmount.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api } from "@/lib/api";
import type { Memory, MemoryVersion } from "@/lib/types";

type Phase = "idle" | "seeding" | "signal" | "detected" | "resolved" | "error";

const OLD = "Prefers dark interfaces over light ones.";
const NEW = "I have switched to light mode.";

export default function ConflictDemo() {
  const { refresh, select } = useMemoryStore();
  const [phase, setPhase] = useState<Phase>("idle");
  const [before, setBefore] = useState<Memory | null>(null);
  const [after, setAfter] = useState<Memory | null>(null);
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, []);

  const wait = (ms: number) => new Promise<void>((resolve) => {
    timers.current.push(setTimeout(resolve, ms));
  });

  const run = useCallback(async () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setAfter(null);
    setVersions([]);
    setMessage(null);
    setPhase("seeding");

    try {
      // Ensure the "old" memory exists (idempotent: reinforced if already there).
      const seeded = await api.createMemory(OLD, "PREFERENCE", 0.7, "conflict-demo");
      if (!alive.current) return;
      const original = seeded.memory;
      setBefore(original);

      await wait(700);
      if (!alive.current) return;
      setPhase("signal");

      await wait(800);
      if (!alive.current) return;

      // Real conflicting signal -> backend resolves it.
      const result = await api.createMemory(NEW, undefined, 0.8, "conflict-demo");
      if (!alive.current) return;

      setPhase("detected");
      await wait(800);
      if (!alive.current) return;

      // Render exactly the object the server returned.
      setAfter(result.memory);
      const detail = await api.memory(result.memory.id);
      if (!alive.current) return;
      setVersions(detail.versions);
      setPhase("resolved");
      if (result.action !== "updated") {
        setMessage("The signal was stored as a new memory (no conflicting memory was close enough).");
      }
      await refresh();
    } catch (e) {
      if (!alive.current) return;
      setPhase("error");
      setMessage(e instanceof Error ? e.message : "The conflict demo could not run.");
    }
  }, [refresh]);

  const steps: { key: Phase; label: string }[] = [
    { key: "seeding", label: "Existing memory" },
    { key: "signal", label: "New signal" },
    { key: "detected", label: "Conflict detected" },
    { key: "resolved", label: "Memory updated" },
  ];
  const order: Phase[] = ["idle", "seeding", "signal", "detected", "resolved"];
  const idx = order.indexOf(phase);

  return (
    <div style={{ display: "grid", gap: "1.8rem" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {steps.map((s, i) => {
          const done = idx > i;
          const active = order[idx] === s.key;
          return (
            <span key={s.key} className="mono" style={{
              padding: "0.4rem 0.7rem", fontSize: "0.5625rem", letterSpacing: "0.14em",
              border: `1px solid ${done || active ? "var(--accent-line)" : "var(--line)"}`,
              color: done || active ? "var(--accent)" : "var(--muted)",
              background: active ? "var(--accent-dim)" : "transparent",
              transition: "all .45s var(--ease)",
            }}>
              {String(i + 1).padStart(2, "0")} {s.label.toUpperCase()}
            </span>
          );
        })}
      </div>

      <div className="conflict-grid" style={{ display: "grid", gap: "1rem" }}>
        <article className="panel" style={{
          padding: "1.4rem", opacity: phase === "resolved" ? 0.45 : 1,
          transition: "opacity .6s var(--ease)",
        }}>
          <p className="label">Old memory · V01</p>
          <p className="subhead" style={{ fontSize: "1.25rem", marginTop: "0.7rem",
               textDecoration: phase === "resolved" ? "line-through" : "none" }}>
            {before?.content ?? OLD}
          </p>
        </article>

        <article className="panel" style={{
          padding: "1.4rem",
          borderColor: idx >= 2 ? "var(--accent-line)" : "var(--line)",
          background: idx >= 2 ? "var(--accent-dim)" : undefined,
          transition: "all .6s var(--ease)",
        }}>
          <p className="label label-accent">New signal</p>
          <p className="subhead" style={{ fontSize: "1.25rem", marginTop: "0.7rem" }}>{NEW}</p>
        </article>

        <article className="panel" style={{
          padding: "1.4rem",
          borderColor: phase === "resolved" ? "var(--accent-line)" : "var(--line)",
          transition: "all .6s var(--ease)",
        }}>
          <p className="label label-accent">
            {after ? `Resolved · V${String(after.version).padStart(2, "0")}` : "Resolution"}
          </p>
          <p className="subhead" style={{ fontSize: "1.25rem", marginTop: "0.7rem",
                                          color: after ? "var(--warm)" : "var(--muted)" }}>
            {after?.content ?? "Awaiting resolution…"}
          </p>
          {after && (
            <button className="btn" style={{ marginTop: "1rem" }}
                    onClick={() => select(after.id)}>
              Inspect memory
            </button>
          )}
        </article>
      </div>

      {versions.length > 1 && (
        <div className="panel" style={{ padding: "1.2rem 1.4rem" }}>
          <p className="label">Stored version history</p>
          <ol style={{ listStyle: "none", padding: 0, margin: "0.8rem 0 0", display: "grid", gap: "0.6rem" }}>
            {versions.map((v) => (
              <li key={v.version} style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
                <span className="mono" style={{ color: "var(--accent)", minWidth: 34 }}>
                  V{String(v.version).padStart(2, "0")}
                </span>
                <span className="body" style={{ fontSize: "0.875rem", flex: "1 1 220px" }}>{v.content}</span>
                <span className="mono" style={{ color: "var(--muted)" }}>{v.reason}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {message && <p className="body" style={{ color: phase === "error" ? "#ff8a7a" : "var(--silver)" }}>{message}</p>}

      <div>
        <button className="btn btn-primary" onClick={() => void run()}
                disabled={phase !== "idle" && phase !== "resolved" && phase !== "error"}
                data-cursor="cta">
          {phase === "idle" ? "Run conflict resolution" : phase === "resolved" ? "Run again" : "Running…"}
        </button>
      </div>

      <style>{`
        @media (min-width: 900px) { .conflict-grid { grid-template-columns: repeat(3, 1fr); } }
      `}</style>
    </div>
  );
}
