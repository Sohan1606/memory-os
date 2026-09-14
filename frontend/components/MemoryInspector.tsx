"use client";
/**
 * Inspector drawer. Always reflects the CURRENT selected memory from the shared
 * store; if that memory disappears (deleted elsewhere) the drawer closes safely.
 */
import { useEffect, useState } from "react";

import { useMemoryStore, useSelectedMemory } from "@/hooks/useMemoryStore";
import { api } from "@/lib/api";
import type { MemoryVersion } from "@/lib/types";

const CATEGORIES = ["IDENTITY", "PREFERENCE", "PROJECT", "GOAL", "HABIT",
  "CONTEXT", "RELATIONSHIP", "FACT", "COMMUNICATION_STYLE"];

function fmt(ts: string) {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(undefined,
    { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function MemoryInspector() {
  const { memories, select, updateMemory, deleteMemory } = useMemoryStore();
  const memory = useSelectedMemory();

  const [draft, setDraft] = useState("");
  const [category, setCategory] = useState("CONTEXT");
  const [editing, setEditing] = useState(false);
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!memory) { setVersions([]); setEditing(false); return; }
    setDraft(memory.content);
    setCategory(memory.category);
    setError(null);
    let cancelled = false;
    api.memory(memory.id)
      .then((d) => { if (!cancelled) setVersions(d.versions); })
      .catch(() => { if (!cancelled) setVersions([]); });
    return () => { cancelled = true; };
  }, [memory]);

  if (!memory) return null;

  const related = memory.related_memory_ids
    .map((id) => memories.find((m) => m.id === id))
    .filter((m): m is NonNullable<typeof m> => Boolean(m));

  const save = async () => {
    if (!draft.trim()) { setError("Memory content cannot be empty."); return; }
    setBusy(true);
    try {
      await updateMemory(memory.id, { content: draft.trim(), category, reason: "Edited in inspector" });
      const fresh = await api.memory(memory.id);
      setVersions(fresh.versions);
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally { setBusy(false); }
  };

  const remove = async () => {
    setBusy(true);
    try { await deleteMemory(memory.id); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not delete."); }
    finally { setBusy(false); }
  };

  const toggleImportant = async () => {
    setBusy(true);
    try { await updateMemory(memory.id, { importance: memory.importance >= 0.9 ? 0.5 : 0.95,
                                          reason: "Importance toggled" }); }
    finally { setBusy(false); }
  };

  return (
    <aside
      role="dialog"
      aria-label="Memory inspector"
      className="inspector panel"
      style={{
        position: "fixed", right: 0, top: 0, bottom: 0, zIndex: 880,
        width: "min(430px, 100vw)", background: "var(--graphite-900)",
        borderLeft: "1px solid var(--line-strong)", borderRadius: 0,
        overflowY: "auto", padding: "1.6rem 1.4rem",
        animation: "inspectorIn .5s var(--ease)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
        <div>
          <p className="label label-accent">Memory</p>
          <p className="mono" style={{ color: "var(--muted)", marginTop: "0.35rem" }}>{memory.id}</p>
        </div>
        <button onClick={() => select(null)} aria-label="Close inspector"
                style={{ background: "none", border: "1px solid var(--line)", padding: "0.4rem 0.7rem", cursor: "pointer" }}>
          <span className="label">Close</span>
        </button>
      </div>

      <div style={{ marginTop: "1.4rem" }}>
        {editing ? (
          <>
            <label className="label" htmlFor="mem-content">Content</label>
            <textarea id="mem-content" className="field" rows={4} value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      style={{ marginTop: "0.5rem", resize: "vertical" }} />
            <label className="label" htmlFor="mem-cat" style={{ display: "block", marginTop: "1rem" }}>
              Category
            </label>
            <select id="mem-cat" className="field" value={category}
                    onChange={(e) => setCategory(e.target.value)} style={{ marginTop: "0.5rem" }}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
            </select>
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
              <button className="btn btn-primary" onClick={save} disabled={busy}>Save</button>
              <button className="btn" onClick={() => { setEditing(false); setDraft(memory.content); }}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <p className="subhead" style={{ fontSize: "1.4rem", lineHeight: 1.25 }}>{memory.content}</p>
        )}
      </div>

      {error && <p className="mono" style={{ color: "#ff8a7a", marginTop: "0.8rem" }}>{error}</p>}

      <dl style={{ marginTop: "1.8rem", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem 0.75rem" }}>
        <Field label="Category" value={memory.category.replace(/_/g, " ")} accent />
        <Field label="Status" value={memory.status} />
        <Field label="Confidence" value={`${Math.round(memory.confidence * 100)}%`} />
        <Field label="Importance" value={`${Math.round(memory.importance * 100)}%`} />
        <Field label="Version" value={`v${memory.version}`} />
        <Field label="Reinforced" value={`${memory.reinforcement_count}×`} />
        <Field label="Source" value={memory.source} />
        <Field label="Thread" value={memory.thread_id ?? "—"} />
        <Field label="Created" value={fmt(memory.created_at)} />
        <Field label="Updated" value={fmt(memory.updated_at)} />
      </dl>

      {versions.length > 1 && (
        <section style={{ marginTop: "1.8rem" }}>
          <p className="label">Version history</p>
          <ol style={{ listStyle: "none", padding: 0, margin: "0.8rem 0 0" }}>
            {versions.map((v) => (
              <li key={v.version} style={{
                borderLeft: `1px solid ${v.version === memory.version ? "var(--accent)" : "var(--line)"}`,
                paddingLeft: "0.9rem", paddingBottom: "0.9rem",
              }}>
                <p className="mono" style={{ color: v.version === memory.version ? "var(--accent)" : "var(--muted)" }}>
                  V{String(v.version).padStart(2, "0")} · {fmt(v.created_at)}
                </p>
                <p className="body" style={{ fontSize: "0.875rem", margin: "0.3rem 0 0" }}>{v.content}</p>
                <p className="mono" style={{ color: "var(--muted)", marginTop: "0.2rem" }}>{v.reason}</p>
              </li>
            ))}
          </ol>
        </section>
      )}

      {related.length > 0 && (
        <section style={{ marginTop: "1.4rem" }}>
          <p className="label">Related memories</p>
          <ul style={{ listStyle: "none", padding: 0, margin: "0.8rem 0 0", display: "grid", gap: "0.4rem" }}>
            {related.map((r) => (
              <li key={r.id}>
                <button onClick={() => select(r.id)} className="panel"
                        style={{ width: "100%", textAlign: "left", padding: "0.6rem 0.75rem",
                                 background: "transparent", cursor: "pointer" }}>
                  <span className="label label-accent">{r.category.replace(/_/g, " ")}</span>
                  <span className="body" style={{ display: "block", fontSize: "0.8125rem", marginTop: "0.2rem" }}>
                    {r.content}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "2rem" }}>
        {!editing && <button className="btn" onClick={() => setEditing(true)} disabled={busy}>Edit</button>}
        <button className="btn" onClick={toggleImportant} disabled={busy}>
          {memory.importance >= 0.9 ? "Unmark important" : "Mark important"}
        </button>
        <button className="btn" onClick={remove} disabled={busy}
                style={{ borderColor: "rgba(255,138,122,0.4)", color: "#ff8a7a" }}>
          Delete
        </button>
      </div>

      <style>{`
        @keyframes inspectorIn { from { transform: translateX(30px); opacity: 0; } to { transform:none; opacity:1; } }
        @media (max-width: 520px) { .inspector { width: 100vw !important; } }
      `}</style>
    </aside>
  );
}

function Field({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mono" style={{
        margin: "0.25rem 0 0", color: accent ? "var(--accent)" : "var(--warm)",
        wordBreak: "break-word",
      }}>{value}</dd>
    </div>
  );
}
