"use client";
/**
 * ZORQ System surface (Z-UI.1 WP-UI-6 — transforms /architecture).
 * Runtime status, the five-plane architecture with live truthful states,
 * the preserved MEMORY//OS stack explainer (live health-driven), and the
 * never-faked principle.
 */
import Link from "next/link";

import Architecture from "@/components/Architecture";
import {
  ErrorPanel, LoadingPanel, MetaBadge, PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { zorqApi } from "@/lib/api";
import { providerPosture } from "@/lib/provider";

export default function SystemPage() {
  const zorq = useFacade(zorqApi.status, 15_000);
  const { health } = useMemoryStore();
  const st = zorq.data?.available ? zorq.data : null;
  const posture = providerPosture(health);

  return (
    <div className="z-page">
      <PageHeader
        kicker="Runtime · System"
        title="System"
        lede="The live state of the runtime and the architecture that governs it. Every status here is produced by the backend — provider posture, retrieval mode, plane status — never assumed."
        right={
          health ? (
            <StateToken
              value={health.status === "ok" ? "READY" : health.status.toUpperCase()}
              note="Live backend health."
            />
          ) : undefined
        }
      />

      <div className="z-grid" data-cols="2" style={{ marginBottom: "1.2rem" }}>
        <ZPanel title="Runtime" hint="From the live /api/health report.">
          {!health && <LoadingPanel rows={4} />}
          {health && (
            <>
              <Readout k="Provider" muted>
                {health.provider.name.toUpperCase()}{health.provider.model ? ` · ${health.provider.model}` : ""}
              </Readout>
              <Readout k="Mode">
                <MetaBadge muted={posture.state !== "REAL"} note={posture.note}>
                  {posture.state === "REAL" ? "REAL AGENT" : posture.state === "FALLBACK" ? "DETERMINISTIC FALLBACK" : "UNKNOWN"}
                </MetaBadge>
              </Readout>
              <Readout k="Tool calling">
                <StateToken
                  value={health.provider.tool_calling ? "AVAILABLE" : "NOT-CONFIGURED"}
                  note={health.provider.detail}
                />
              </Readout>
              <Readout k="Retrieval mode" muted>
                {health.vector.mode.toUpperCase()}{health.vector.error ? " (fallback)" : ""}
              </Readout>
              <Readout k="Embeddings" muted>
                {health.embeddings.model ?? "—"}{health.embeddings.dimension ? ` · ${health.embeddings.dimension}d` : ""}
              </Readout>
              <Readout k="Memory store" muted>
                {health.memory.count} memories · {health.vector.count} vectors
              </Readout>
              <Readout k="Agent graph" muted>
                {health.agent.framework} · {health.agent.graph} · {health.agent.checkpointer}
              </Readout>
              <Readout k="LangMem extraction">
                <StateToken value={health.langmem.state.replace(/ /g, "-")} note={health.langmem.detail} />
              </Readout>
              <Readout k="Voice input">
                <StateToken
                  value={health.voice.mode === "browser" ? "BROWSER-FALLBACK" : health.voice.mode.toUpperCase()}
                  note={`${health.voice.detail} The 3F-min browser voice transport (speech input, tracked speech output, local barge-in) is implemented; browser recognition may send audio to the browser vendor's speech service. 3F-full generation-level voice control is deferred.`}
                />
              </Readout>
            </>
          )}
        </ZPanel>

        <ZPanel title="ZORQ core" hint="From the ZORQ state facade.">
          {zorq.condition === "LOADING" && <LoadingPanel rows={4} />}
          {zorq.condition === "ERROR" && <ErrorPanel retry={zorq.refresh} />}
          {zorq.condition === "UNAVAILABLE" && zorq.reason && <UnavailablePanel reason={zorq.reason} retry={zorq.refresh} />}
          {st && (
            <>
              <Readout k="Package" muted>{st.package} {st.package_version}</Readout>
              <Readout k="Security epoch" muted>{st.core.security_epoch}</Readout>
              <Readout k="Filesystem posture"><StateToken value={st.core.filesystem_posture} /></Readout>
              <Readout k="Audit integrity"><StateToken value={st.core.audit_integrity ? "VERIFIED" : "UNKNOWN"} /></Readout>
              <Readout k="Memory authority" muted>{st.memory_integration.canonical_authority}</Readout>
              <Readout k="Capabilities" muted>{st.capabilities_summary.count} sealed</Readout>
              <Readout k="Deep inspection" muted><Link href="/observatory" className="z-chip">Open Observatory</Link></Readout>
            </>
          )}
        </ZPanel>
      </div>

      {st && (
        <section aria-label="Five planes" style={{ marginBottom: "1.6rem" }}>
          <p className="z-nav-group" style={{ marginBottom: "0.8rem" }}>The five planes</p>
          <div className="z-grid" data-cols="2">
            {st.planes.map((plane) => (
              <ZPanel
                key={plane.plane}
                title={plane.plane}
                right={<StateToken value={plane.status} note={plane.status_note} />}
              >
                <p style={{ fontSize: "0.8rem", color: "var(--z-ink-2)", lineHeight: 1.55, margin: "0 0 0.6rem" }}>
                  {plane.responsibility}
                </p>
                <p style={{
                  fontSize: "0.72rem", color: "var(--z-ink-3)", lineHeight: 1.5, margin: 0,
                  borderLeft: "2px solid var(--z-accent-line)", paddingLeft: "0.7rem",
                }}>
                  Boundary — {plane.boundary}
                </p>
              </ZPanel>
            ))}
          </div>
          <p style={{ fontSize: "0.75rem", color: "var(--z-ink-3)", margin: "0.8rem 0 0" }}>
            No plane silently inherits authority from another.
          </p>
        </section>
      )}

      <section aria-label="MEMORY//OS stack" style={{ marginBottom: "1.6rem" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "0.8rem", marginBottom: "0.7rem" }}>
          <span style={{
            fontFamily: "var(--mono)", fontSize: "0.7rem", letterSpacing: "0.14em",
            color: "var(--accent)",
          }}>{"MEMORY"}<span style={{ opacity: 0.7 }}>{"//"}</span>{"OS"}</span>
          <span className="z-nav-group" style={{ margin: 0 }}>the memory subsystem — live stack</span>
        </div>
        <Architecture />
      </section>

      <ZPanel title="Never faked" hint="The principle this interface is held to.">
        <p style={{ fontSize: "0.82rem", color: "var(--z-ink-2)", lineHeight: 1.6, margin: 0 }}>
          Nothing on this system claims a capability the backend does not have.
          Unknown renders as UNKNOWN. Unconfigured renders as NOT-CONFIGURED.
          Not-yet-built renders as NOT-IMPLEMENTED with its phase. A completed
          action is never shown as verified, and a visible capability is never
          shown as permission.
        </p>
      </ZPanel>
    </div>
  );
}
