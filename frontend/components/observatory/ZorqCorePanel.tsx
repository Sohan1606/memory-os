"use client";
/**
 * ZORQ core inspection panel for the Observatory (Z-UI.1 WP-UI-5).
 * Everything renders from the ZORQ state facade — the same read-model the
 * other surfaces use — so Observatory remains one source of truth for
 * subsystem inspection, now including the ZORQ control plane.
 */
import Link from "next/link";

import {
  ErrorPanel, LoadingPanel, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { zorqApi } from "@/lib/api";

/**
 * The five-plane architecture is a structural constant of the system — the
 * plane names and responsibilities render regardless of facade availability.
 * Plane STATUSES are live values: they render from the state facade when it
 * is reachable, and as UNAVAILABLE otherwise — never invented.
 */
const PLANES = [
  { plane: "Intelligence", responsibility: "Observe, orient, interrogate, research, simulate, decide, plan, optimize, propose." },
  { plane: "Continuity", responsibility: "Store, retrieve, track, connect, version, delete, and govern personal history through the MEMORY//OS boundary." },
  { plane: "Interaction", responsibility: "Text/voice/file interaction, response generation, speech, interruption, pause/resume, branches, checkpoints." },
  { plane: "Action", responsibility: "Authorize, snapshot, lease, execute, cancel, verify, audit." },
  { plane: "Evolution", responsibility: "Evaluate, propose experiments, canary behavioral changes, monitor and roll back." },
];

export default function ZorqCorePanel() {
  const zorq = useFacade(zorqApi.status, 10_000);
  const st = zorq.data?.available ? zorq.data : null;

  // Normalized plane rows: live statuses from the facade when reachable,
  // otherwise the structural planes with an honest UNAVAILABLE status.
  const planeRows: { plane: string; status: string | null; note: string }[] = st
    ? st.planes.map((p) => ({
        plane: p.plane, status: p.status, note: p.status_note ?? p.responsibility,
      }))
    : PLANES.map((p) => ({ plane: p.plane, status: null, note: p.responsibility }));

  return (
    <ZPanel
      title="ZORQ core"
      hint="The ZORQ control plane over this runtime. Rendered from the state facade."
      style={{ gridColumn: "1 / -1" }}
    >
      {zorq.condition === "LOADING" && <LoadingPanel rows={3} />}
      {zorq.condition === "ERROR" && <ErrorPanel retry={zorq.refresh} />}
      {zorq.condition === "UNAVAILABLE" && zorq.reason && <UnavailablePanel reason={zorq.reason} retry={zorq.refresh} />}
      <div className="z-grid" data-cols="4" style={{ gap: "0.9rem" }}>
        {st && (
          <>
            <div>
              <Readout k="Package" muted>{st.package} {st.package_version}</Readout>
              <Readout k="Security epoch" muted>{st.core.security_epoch}</Readout>
              <Readout k="Sessions" muted>{st.core.active_sessions} (facade holds none)</Readout>
              <Readout k="Capabilities" muted>{st.capabilities_summary.count} sealed</Readout>
            </div>
            <div>
              <Readout k="Connection"><StateToken value={st.runtime.connection} note={st.runtime.connection_note} /></Readout>
              <Readout k="Sync"><StateToken value={st.runtime.sync} note={st.runtime.sync_note} /></Readout>
              <Readout k="Voice"><StateToken value={st.runtime.voice.state} note={st.runtime.voice.note} /></Readout>
              <Readout k="Multimodal"><StateToken value={st.runtime.multimodal.state} note={st.runtime.multimodal.note} /></Readout>
            </div>
            <div>
              <Readout k="Memory authority" muted>{st.memory_integration.canonical_authority}</Readout>
              <Readout k="Adapter">
                <StateToken
                  value={st.memory_integration.production_adapter.integration_status}
                  note={st.memory_integration.production_adapter.verification_record}
                />
              </Readout>
              <Readout k="Audit integrity">
                <StateToken value={st.core.audit_integrity ? "VERIFIED" : "UNKNOWN"} />
              </Readout>
              <Readout k="Filesystem"><StateToken value={st.core.filesystem_posture} /></Readout>
            </div>
          </>
        )}
        <div>
          <p className="z-nav-group" style={{ margin: "0 0 0.5rem" }}>Planes</p>
          {planeRows.map((row) => (
            <div key={row.plane} style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", marginBottom: "0.4rem" }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: "0.64rem", color: "var(--z-ink-3)", letterSpacing: "0.06em" }}>{row.plane}</span>
              <StateToken value={row.status ?? "UNAVAILABLE"} small note={row.note} />
            </div>
          ))}
          {!st && (
            <p style={{ margin: "0.4rem 0 0", fontSize: "0.66rem", color: "var(--z-ink-3)", lineHeight: 1.5 }}>
              Plane statuses render from the state facade; it is unreachable, so none is claimed.
            </p>
          )}
        </div>
      </div>
      {st && (
          <p style={{ margin: "0.9rem 0 0", fontSize: "0.72rem", color: "var(--z-ink-3)" }}>
            Dedicated surfaces:{" "}
            <Link href="/actions" className="z-chip">Actions</Link>{" "}
            <Link href="/capabilities" className="z-chip">Capabilities</Link>{" "}
            <Link href="/audit" className="z-chip">Audit</Link>{" "}
            <Link href="/devices" className="z-chip">Devices</Link>{" "}
            <Link href="/system" className="z-chip">System</Link>
          </p>
      )}
    </ZPanel>
  );
}
