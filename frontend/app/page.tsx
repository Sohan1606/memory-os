"use client";
/**
 * ZORQ System Home — the intelligence operating console (visual transformation).
 *
 * First screen: ZORQ / SYSTEM INTELLIGENCE identity, the central core
 * instrument (real state only), the intelligence-flow contract (dormant —
 * evidence lives in the workspace per turn), the command surface, and a
 * telemetry strip. Deeper exploration descends progressively.
 * Every value renders from the backend; nothing is hardcoded state.
 */
import Link from "next/link";

import LayerSwitcher from "@/components/LayerSwitcher";
import RetrievalDemo from "@/components/RetrievalDemo";
import VoiceDemo from "@/components/VoiceDemo";
import CoreVisualization from "@/components/zorq/CoreVisualization";
import IntelligenceRail from "@/components/zorq/IntelligenceRail";
import {
  ErrorPanel, LoadingPanel, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { api, zorqApi } from "@/lib/api";
import { providerPosture } from "@/lib/provider";
import type { Health } from "@/lib/types";
import { useEffect, useState } from "react";

const SURFACES = [
  {
    group: "Work",
    items: [
      { href: "/memory", title: "Memory", lede: "The MEMORY//OS governance window: live memory store, versions, conflicts, timeline." },
      { href: "/observatory", title: "Observatory", lede: "Deep inspection of every live subsystem." },
    ],
  },
  {
    group: "Control",
    items: [
      { href: "/actions", title: "Actions", lede: "The action lifecycle — authorization, snapshot, lease, execution, verification, audit." },
      { href: "/capabilities", title: "Capabilities", lede: "What ZORQ can do, and what it is permitted to do. Visibility is not permission." },
      { href: "/audit", title: "Audit", lede: "The hash-chained audit trail of the ZORQ core." },
      { href: "/system", title: "System", lede: "Runtime status, the five planes, provider posture." },
    ],
  },
  {
    group: "Runtime",
    items: [
      { href: "/devices", title: "Devices", lede: "This device, connection and synchronization truth." },
    ],
  },
];

export default function HomePage() {
  const zorq = useFacade(zorqApi.status, 15_000);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => api.health().then((h) => {
      if (!cancelled) { setHealth(h); setHealthError(false); }
    }).catch(() => { if (!cancelled) setHealthError(true); });
    load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 15_000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, []);

  const st = zorq.data?.available ? zorq.data : null;
  const posture = providerPosture(health);

  return (
    <div className="z-page">
      {/* ---------------------------------------- identity + core instrument */}
      <section aria-label="ZORQ core" style={{ marginBottom: "1.6rem" }}>
        <div style={{ textAlign: "center", marginBottom: "1.4rem" }}>
          <p style={{
            fontFamily: "var(--mono)", fontSize: "0.58rem", fontWeight: 600,
            letterSpacing: "0.34em", color: "var(--z-accent)", margin: "0 0 0.7rem",
          }}>ZORQ</p>
          <h1 className="z-page-title" style={{ fontSize: "clamp(1.5rem, 3.2vw, 2.1rem)" }}>
            System Intelligence
          </h1>
          <p className="z-page-lede" style={{ margin: "0.7rem auto 0" }}>
            An intelligence system with real boundaries — canonical memory, a governed
            action plane, and a runtime that reports its true state.
          </p>
        </div>

        {zorq.condition === "ERROR" && <ErrorPanel retry={zorq.refresh} detail="The ZORQ facade could not be reached." />}
        {zorq.condition === "UNAVAILABLE" && zorq.reason && <UnavailablePanel reason={zorq.reason} retry={zorq.refresh} />}
        {(zorq.condition === "LOADING" || st) && (
          <CoreVisualization zorq={st} health={health} size={340} />
        )}
      </section>

      {/* ------------------------------ intelligence flow contract (dormant) */}
      <section aria-label="Intelligence flow contract" style={{ marginBottom: "1.6rem" }}>
        <IntelligenceRail turn={null} />
      </section>

      {/* ------------------------------------------- command / work surface */}
      <section aria-label="Primary workspace" style={{ marginBottom: "1.5rem" }}>
        <Link href="/workspace" className="z-workspace-band">
          <span className="z-nav-group" style={{ margin: 0, color: "var(--z-accent)" }}>Command surface</span>
          <span style={{
            fontFamily: "var(--mono)", fontSize: "clamp(1.05rem, 2.2vw, 1.45rem)", fontWeight: 600,
            letterSpacing: "0.18em", color: "var(--z-ink)",
          }}>OPEN THE WORKSPACE</span>
          <span style={{ fontSize: "0.8rem", color: "var(--z-ink-3)", lineHeight: 1.55, maxWidth: "52ch" }}>
            Conversation with recalled memory and provenance, the intelligence flow
            rendered from real per-turn evidence, and any proposed action behind its
            authorization gate.
          </span>
          <span aria-hidden="true" style={{ color: "var(--z-accent)", fontSize: "1.25rem", marginLeft: "auto", alignSelf: "center" }}>→</span>
        </Link>
      </section>

      {/* ------------------------------------------------- telemetry strip */}
      <section aria-label="System telemetry" className="z-telemetry" style={{ marginBottom: "2.2rem" }}>
        <span className="z-telemetry-item" title="Truthful connection state from the facade">
          CONNECTION <b>{st ? st.runtime.connection : "…"}</b>
        </span>
        <span className="z-telemetry-item" title="Canonical memory and governance subsystem">
          MEMORY//OS <b>{st ? st.memory_integration.production_adapter.integration_status.replace("_", " ") : "…"}</b>
        </span>
        <span className="z-telemetry-item" title={posture.note}>
          PROVIDER <b>{posture.state === "REAL" ? "REAL AGENT" : posture.state === "FALLBACK" ? "DETERMINISTIC FALLBACK" : "UNKNOWN"}</b>
        </span>
        <span className="z-telemetry-item" title="Backend health">
          SYSTEM <b>{health ? health.status.toUpperCase() : healthError ? "UNAVAILABLE" : "…"}</b>
        </span>
        {st && (
          <span className="z-telemetry-item" title="Sealed capability registry">
            CAPABILITIES <b>{st.capabilities_summary.count}</b>
          </span>
        )}
      </section>

      {/* ------------------------------------------------ live system state */}
      <section aria-label="Live system state" style={{ marginBottom: "2.4rem" }}>
        <p className="z-nav-group" style={{ marginBottom: "0.8rem" }}>System state</p>
        <div className="z-grid" data-cols="3">
          <ZPanel title="Runtime" hint="Rendered from the ZORQ state facade. Connection LOCAL is today's truthful state.">
            {zorq.condition === "LOADING" && <LoadingPanel rows={3} />}
            {zorq.condition === "ERROR" && <ErrorPanel retry={zorq.refresh} detail="The ZORQ facade could not be reached." />}
            {zorq.condition === "UNAVAILABLE" && zorq.reason && <UnavailablePanel reason={zorq.reason} retry={zorq.refresh} />}
            {st && (
              <>
                <Readout k="Connection"><StateToken value={st.runtime.connection} note={st.runtime.connection_note} /></Readout>
                <Readout k="Execution origin" muted>LOCAL — this device</Readout>
                <Readout k="Synchronization"><StateToken value={st.runtime.sync} note={st.runtime.sync_note} /></Readout>
                <Readout k="Voice"><StateToken value={st.runtime.voice.state} note={`${st.runtime.voice.note} (${st.runtime.voice.phase})`} /></Readout>
                <Readout k="Multimodal"><StateToken value={st.runtime.multimodal.state} note={`${st.runtime.multimodal.note} (${st.runtime.multimodal.phase})`} /></Readout>
              </>
            )}
          </ZPanel>

          <ZPanel title="Memory authority" hint="MEMORY//OS is the canonical memory and governance subsystem; ZORQ renders its state and never replaces it.">
            {(() => {
              if (!st) return <LoadingPanel rows={3} />;
              const adapter = st.memory_integration.production_adapter;
              return (
                <>
                  <Readout k="Authority" muted>{st.memory_integration.canonical_authority}</Readout>
                  <Readout k="Production adapter">
                    <StateToken value={adapter.integration_status} note={`${adapter.contract_version ?? ""} ${adapter.verification_record ?? ""}`} />
                  </Readout>
                  <Readout k="Memories (live)" muted>{health ? String(health.memory.count) : healthError ? "UNAVAILABLE" : "…"}</Readout>
                  <Readout k="Retrieval mode" muted>{health?.vector.mode === "semantic" ? "SEMANTIC (local embeddings)" : health ? "KEYWORD FALLBACK" : "…"}</Readout>
                  <Readout k="Embeddings" muted>{health?.embeddings.model ?? "—"}</Readout>
                </>
              );
            })()}
          </ZPanel>

          <ZPanel title="Control plane" hint="The Phase 2.6 action plane is implemented and test-verified; its HTTP exposure arrives with the action surfaces phase.">
            {st ? (
              <>
                <Readout k="Package" muted>{st.package} {st.package_version}</Readout>
                <Readout k="Security epoch" muted>{st.core.security_epoch}</Readout>
                <Readout k="Filesystem posture"><StateToken value={st.core.filesystem_posture} /></Readout>
                <Readout k="Active sessions" muted>{st.core.active_sessions} (facade holds none)</Readout>
                <Readout k="Audit integrity">
                  <StateToken value={st.core.audit_integrity ? "VERIFIED" : "UNKNOWN"} note="Hash-chained audit log integrity" />
                </Readout>
                <Readout k="Capabilities" muted>{st.capabilities_summary.count} sealed</Readout>
              </>
            ) : (
              <LoadingPanel rows={4} />
            )}
          </ZPanel>
        </div>
      </section>

      {/* ---------------------------------------------------- the five planes */}
      {st && (
        <section aria-label="Architecture planes" style={{ marginBottom: "2.4rem" }}>
          <p className="z-nav-group" style={{ marginBottom: "0.8rem" }}>The five planes — live status</p>
          <div className="z-grid" data-cols="5">
            {st.planes.map((plane) => (
              <ZPanel key={plane.plane} title={plane.plane} style={{ padding: "0.9rem 1rem" }}>
                <div style={{ marginBottom: "0.55rem" }}>
                  <StateToken value={plane.status} title={plane.status_note ?? plane.responsibility} />
                </div>
                <p style={{ fontSize: "0.7rem", color: "var(--z-ink-3)", lineHeight: 1.5, margin: 0 }}>
                  {plane.responsibility}
                </p>
              </ZPanel>
            ))}
          </div>
          <p style={{ fontSize: "0.75rem", color: "var(--z-ink-3)", margin: "0.8rem 0 0" }}>
            No plane silently inherits authority from another. Memory retrieval is not authorization; the action plane cannot be bypassed.
          </p>
        </section>
      )}

      {/* --------------------------------------------------- surface directory */}
      <section aria-label="Surfaces" style={{ marginBottom: "2.6rem" }}>
        <p className="z-nav-group" style={{ marginBottom: "0.9rem" }}>Surfaces</p>
        {SURFACES.map((group) => (
          <div key={group.group} style={{ marginBottom: "1.4rem" }}>
            <p className="z-nav-group" style={{ marginBottom: "0.55rem", opacity: 0.55 }}>{group.group}</p>
            <div className="z-grid" data-cols="3">
              {group.items.map((item) => (
                <Link key={item.href} href={item.href} className="z-panel" style={{ textDecoration: "none", display: "block" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <h3 className="z-panel-title" style={{ color: "var(--z-ink)" }}>{item.title}</h3>
                    <span aria-hidden="true" style={{ color: "var(--z-accent)", fontSize: "0.8rem" }}>→</span>
                  </div>
                  <p style={{ fontSize: "0.78rem", color: "var(--z-ink-3)", lineHeight: 1.55, margin: "0.55rem 0 0" }}>
                    {item.lede}
                  </p>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </section>

      {/* ------------------------------ MEMORY//OS live demonstrations (preserved) */}
      <section aria-label="MEMORY//OS live demonstrations" style={{ marginBottom: "2rem" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "0.8rem", marginBottom: "0.4rem" }}>
          <span style={{
            fontFamily: "var(--mono)", fontSize: "0.7rem", letterSpacing: "0.14em",
            color: "var(--accent)",
          }}>{"MEMORY"}<span style={{ opacity: 0.7 }}>{"//"}</span>{"OS"}</span>
          <span className="z-nav-group" style={{ margin: 0 }}>the memory subsystem — live demonstrations</span>
        </div>
        <p className="z-page-lede" style={{ marginBottom: "1.4rem" }}>
          Real pathways over the live store: retrieval with reasons, memory layers, and speech-to-memory.
          These run against the actual backend — nothing here is a mock.
        </p>
        <div className="z-grid" data-cols="2">
          <RetrievalDemo />
          <LayerSwitcher />
        </div>
        <div style={{ marginTop: "1rem" }}>
          <VoiceDemo />
        </div>
      </section>
    </div>
  );
}
