"use client";
/**
 * Memory explorer: the full live store, filterable, plus the network graph,
 * the audit timeline and a real consolidation action.
 */
import { useMemo, useState } from "react";

import ConflictDemo from "@/components/ConflictDemo";
import Footer from "@/components/Footer";
import MemoryGraph from "@/components/MemoryGraph";
import MemoryTimeline from "@/components/MemoryTimeline";
import Section from "@/components/Section";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api } from "@/lib/api";

export default function MemoryPage() {
  const { memories, stats, loading, error, select, refresh } = useMemoryStore();
  const [category, setCategory] = useState<string>("ALL");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const categories = useMemo(() => {
    const set = new Set(memories.map((m) => m.category));
    return ["ALL", ...Array.from(set).sort()];
  }, [memories]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return memories.filter((m) =>
      (category === "ALL" || m.category === category) &&
      (!needle || m.content.toLowerCase().includes(needle)));
  }, [memories, category, q]);

  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const consolidate = async () => {
    if (picked.length < 2) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await api.consolidate(picked);
      setNote(`Consolidated ${picked.length} memories into one.`);
      setPicked([]);
      await refresh();
      select(res.memory.id);
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Consolidation failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Section
        index="—"
        label="Memory explorer"
        title={<>Everything the system knows.</>}
        lede="This is the live database, not a sample. Select any memory to inspect its versions, relationships, confidence and provenance — or edit and delete it."
        wide
      >
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", marginBottom: "1.5rem" }}>
          <label htmlFor="mem-filter" className="sr-only">Filter memories</label>
          <input id="mem-filter" className="field" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Filter by text…" style={{ flex: "1 1 240px" }} />
          {picked.length >= 2 && (
            <button className="btn btn-primary" onClick={() => void consolidate()} disabled={busy}>
              Consolidate {picked.length}
            </button>
          )}
        </div>

        <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginBottom: "1.5rem" }}>
          {categories.map((c) => (
            <button key={c} className="chip" data-active={category === c} onClick={() => setCategory(c)}>
              {c.replace(/_/g, " ")}
              {c !== "ALL" && stats ? ` · ${stats.by_category[c] ?? 0}` : ""}
            </button>
          ))}
        </div>

        {note && <p className="body" style={{ color: "var(--accent)" }}>{note}</p>}
        {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
        {loading && memories.length === 0 && <p className="label">Loading memory…</p>}

        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.6rem",
                     gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
          {visible.map((m) => (
            <li key={m.id} className="panel" style={{
              padding: "1.1rem",
              borderColor: picked.includes(m.id) ? "var(--accent-line)" : "var(--line)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.75rem" }}>
                <span className="label label-accent">{m.category.replace(/_/g, " ")}</span>
                <span className="mono" style={{ color: "var(--muted)" }}>v{m.version}</span>
              </div>
              <p className="body" style={{ color: "var(--warm)", margin: "0.6rem 0 1rem" }}>{m.content}</p>
              <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                <button className="btn" onClick={() => select(m.id)}>Inspect</button>
                <button className="chip" data-active={picked.includes(m.id)} onClick={() => toggle(m.id)}>
                  {picked.includes(m.id) ? "Selected" : "Select"}
                </button>
              </div>
            </li>
          ))}
          {!loading && visible.length === 0 && <li className="body">No memories match that filter.</li>}
        </ul>
      </Section>

      <Section index="—" label="Network" title={<>How memories connect.</>}
               lede="Edges are real relationships recorded by the backend when memories reinforce, supersede or consolidate each other." wide>
        <div className="panel" style={{ padding: "0.5rem" }}>
          <MemoryGraph height={560} />
        </div>
      </Section>

      <Section index="—" label="Conflict" title={<>When the truth changes.</>}
               lede="Run a genuine contradiction through the system. The backend detects the conflict, supersedes the old content and increments the version — nothing here is scripted animation." wide>
        <ConflictDemo />
      </Section>

      <Section index="—" label="Audit" title={<>Every change, recorded.</>}
               lede="Creation, reinforcement, update, supersession, consolidation and deletion are all written to an append-only event log." wide>
        <MemoryTimeline />
      </Section>

      <Footer />
    </>
  );
}
