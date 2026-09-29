"use client";
/**
 * ZORQ Memory surface (Z-UI.1 WP-UI-4 — extends the memory explorer).
 * All MEMORY//OS explorer functionality is preserved: the live store with
 * filtering and consolidation, the relationship graph, the real conflict
 * pathway, and the audit timeline. Added: the governance header — canonical
 * authority, production adapter status, and the retrieval/governance
 * distinction, rendered from the ZORQ facade.
 */
import Link from "next/link";
import { useMemo, useState } from "react";

import ConflictDemo from "@/components/ConflictDemo";
import MemoryGraph from "@/components/MemoryGraph";
import MemoryTimeline from "@/components/MemoryTimeline";
import {
  ErrorPanel, LoadingPanel, MetaBadge, PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { api, zorqApi } from "@/lib/api";

export default function MemoryPage() {
  const { memories, stats, loading, error, select, refresh } = useMemoryStore();
  const zorq = useFacade(zorqApi.status, 30_000);
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

  const st = zorq.data?.available ? zorq.data : null;
  const adapter = st?.memory_integration.production_adapter;

  return (
    <div className="z-page">
      <PageHeader
        kicker={<>Primary · <span style={{ color: "var(--accent)" }}>MEMORY{"//"}OS</span></>}
        title="Memory & context"
        lede="The governance window over the canonical memory subsystem. This is the live database, not a sample — every memory, version, conflict and audit event is real."
        right={<MetaBadge note="MEMORY//OS is the canonical memory and governance subsystem; ZORQ renders and uses it, never replaces it.">CANONICAL</MetaBadge>}
      />

      {/* ------------------------------------------------ governance header */}
      <div className="z-grid" data-cols="3" style={{ marginBottom: "2rem" }}>
        <ZPanel title="Authority" hint="Where memory truth lives.">
          {st ? (
            <>
              <Readout k="Canonical authority" muted>{st.memory_integration.canonical_authority}</Readout>
              <Readout k="Contract version" muted>{adapter?.contract_version ?? "—"}</Readout>
              <Readout k="Integration">
                <StateToken value={adapter?.integration_status ?? "UNKNOWN"} note={adapter?.verification_record} />
              </Readout>
            </>
          ) : zorq.condition === "ERROR" ? (
            <ErrorPanel retry={zorq.refresh} />
          ) : zorq.condition === "UNAVAILABLE" && zorq.reason ? (
            <UnavailablePanel reason={zorq.reason} />
          ) : (
            <LoadingPanel rows={3} />
          )}
        </ZPanel>

        <ZPanel title="Retrieval is not governance" hint="Reading a memory and changing a memory are different authorities." wide>
          <p style={{ fontSize: "0.8rem", color: "var(--z-ink-3)", lineHeight: 1.6, margin: 0 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.66rem", letterSpacing: "0.1em", color: "var(--z-ink-2)" }}>
              MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE
            </span>
            <br />
            Recalled memory supplies context to conversation; it never authorizes
            anything. Writes, updates, supersession and deletion pass through the
            governance pathway below — with versions, provenance and audit events —
            regardless of who or what requested them. The memory-event trail is on{" "}
            <Link href="/audit" className="z-chip">the audit surface</Link> and in the timeline below.
          </p>
        </ZPanel>
      </div>

      {/* --------------------------------------------------------- explorer */}
      <section aria-label="Memory explorer">
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", marginBottom: "1.2rem" }}>
          <label htmlFor="mem-filter" className="sr-only">Filter memories</label>
          <input id="mem-filter" className="field" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Filter by text…" style={{ flex: "1 1 240px" }} />
          {picked.length >= 2 && (
            <button className="btn btn-primary" onClick={() => void consolidate()} disabled={busy}>
              Consolidate {picked.length}
            </button>
          )}
        </div>

        <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginBottom: "1.4rem" }}>
          {categories.map((c) => (
            <button key={c} className="chip" data-active={category === c} onClick={() => setCategory(c)}>
              {c.replace(/_/g, " ")}
              {c !== "ALL" && stats ? ` · ${stats.by_category[c] ?? 0}` : ""}
            </button>
          ))}
        </div>

        {note && <p className="body" style={{ color: "var(--accent)" }}>{note}</p>}
        {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
        {loading && memories.length === 0 && (
          <div role="status" aria-label="Loading memories" style={{ display: "grid", gap: "0.7rem" }}>
            {Array.from({ length: 4 }).map((_, i) => <div key={i} className="z-skeleton" style={{ height: "4.5rem" }} />)}
          </div>
        )}

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
      </section>

      {/* ----------------------------------------------------------- network */}
      <section aria-label="Memory network" style={{ marginTop: "3rem" }}>
        <p className="z-nav-group" style={{ marginBottom: "0.7rem" }}>Network</p>
        <p className="z-page-lede" style={{ marginBottom: "1rem" }}>
          Edges are real relationships recorded by the backend when memories reinforce,
          supersede or consolidate each other.
        </p>
        <div className="panel" style={{ padding: "0.5rem" }}>
          <MemoryGraph height={560} />
        </div>
      </section>

      {/* ---------------------------------------------------------- conflict */}
      <section aria-label="Conflict pathway" style={{ marginTop: "3rem" }}>
        <p className="z-nav-group" style={{ marginBottom: "0.7rem" }}>Conflict</p>
        <p className="z-page-lede" style={{ marginBottom: "1rem" }}>
          Run a genuine contradiction through the system. The backend detects the
          conflict, supersedes the old content and increments the version — nothing
          here is scripted animation.
        </p>
        <ConflictDemo />
      </section>

      {/* ------------------------------------------------------------- audit */}
      <section aria-label="Memory audit timeline" style={{ marginTop: "3rem" }}>
        <p className="z-nav-group" style={{ marginBottom: "0.7rem" }}>Audit timeline</p>
        <p className="z-page-lede" style={{ marginBottom: "1rem" }}>
          Creation, reinforcement, update, supersession, consolidation and deletion are
          all written to an append-only event log.
        </p>
        <MemoryTimeline />
      </section>
    </div>
  );
}
