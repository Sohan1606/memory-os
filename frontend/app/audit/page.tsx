"use client";
/**
 * ZORQ Audit surface (Z-UI.1 WP-UI-5).
 * The real hash-chained audit tail of the ZORQ core, read-only. Records are
 * never fabricated; the scope note says exactly what this trail is.
 */
import Link from "next/link";

import {
  ErrorPanel, LoadingPanel, PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { zorqApi } from "@/lib/api";

export default function AuditPage() {
  const audit = useFacade(zorqApi.audit, 15_000);
  const data = audit.data?.available ? audit.data : null;

  return (
    <div className="z-page">
      <PageHeader
        kicker="System · Audit"
        title="Audit trail"
        lede="Append-only, hash-chained records of the ZORQ core. Each event commits to the hash of the one before it, so tampering is detectable — integrity is verified on every read."
        right={(
          <StateToken
            value={data ? (data.integrity_verified ? "VERIFIED" : "UNKNOWN") : "UNKNOWN"}
            note={data
              ? "Chain integrity of the audit log, as verified by the backend."
              : "Chain integrity not established in this read — the state facade is unavailable, so nothing is claimed."}
          />
        )}
      />

      {/* Verification states are a contract of this surface, not live data:
          they render regardless of facade availability. */}
      <ZPanel title="Verification states" hint="What the integrity token means — and never means." style={{ marginBottom: "1.2rem" }}>
        <div style={{ display: "grid", gap: "0.55rem", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}>
          <div style={{ display: "flex", gap: "0.8rem", alignItems: "center", flexWrap: "wrap" }}>
            <StateToken value="VERIFIED" />
            <span style={{ fontSize: "0.78rem", color: "var(--z-ink-3)", flex: 1, minWidth: "12rem" }}>
              The backend re-computed the hash chain and every link held. VERIFIED is evidence-established, never assumed.
            </span>
          </div>
          <div style={{ display: "flex", gap: "0.8rem", alignItems: "center", flexWrap: "wrap" }}>
            <StateToken value="UNKNOWN" />
            <span style={{ fontSize: "0.78rem", color: "var(--z-ink-3)", flex: 1, minWidth: "12rem" }}>
              Integrity was not established in this read — the log exists, but no verification result backs it. Never rendered as success.
            </span>
          </div>
        </div>
      </ZPanel>

      {audit.condition === "LOADING" && <LoadingPanel rows={5} />}
      {audit.condition === "ERROR" && <ErrorPanel retry={audit.refresh} />}
      {audit.condition === "UNAVAILABLE" && audit.reason && <UnavailablePanel reason={audit.reason} retry={audit.refresh} />}

      {data && (
        <div className="z-grid" data-cols="2">
          <ZPanel title="Chain integrity" hint={data.chain}>
            <Readout k="Integrity"><StateToken value={data.integrity_verified ? "VERIFIED" : "UNKNOWN"} /></Readout>
            <Readout k="Events" muted>{data.events.length}</Readout>
            <Readout k="Persistence" muted>{data.persistence}</Readout>
          </ZPanel>

          <ZPanel title="Scope" hint="What this trail truthfully is.">
            <p style={{ fontSize: "0.8rem", color: "var(--z-ink-3)", lineHeight: 1.55, margin: 0 }}>
              {data.scope_note}
            </p>
            <p style={{ fontSize: "0.8rem", color: "var(--z-ink-3)", lineHeight: 1.55, margin: "0.7rem 0 0" }}>
              The MEMORY//OS memory-event timeline (creation, reinforcement, retrieval,
              deletion of memories) is a separate, equally real record:{" "}
              <Link href="/memory" className="z-chip">Memory → audit timeline</Link>
            </p>
          </ZPanel>

          <ZPanel title="Events" hint="Oldest first. Real records only." wide>
            <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "0.55rem" }}>
              {data.events.map((e) => (
                <li key={e.event_id} style={{
                  border: "1px solid var(--z-line)", borderRadius: 6,
                  padding: "0.75rem 0.9rem", display: "grid", gap: "0.3rem",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "0.8rem", flexWrap: "wrap", alignItems: "center" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.7rem", color: "var(--z-ink)", letterSpacing: "0.06em" }}>
                      <span style={{ color: "var(--z-ink-3)" }}>#{e.sequence}</span>{"  "}{e.event_type}
                    </span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem", color: "var(--z-ink-3)" }}>
                      {new Date(e.timestamp).toLocaleString()}
                    </span>
                  </div>
                  {Object.keys(e.payload).length > 0 && (
                    <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: "0.64rem", color: "var(--z-ink-3)", overflowWrap: "anywhere" }}>
                      {Object.entries(e.payload)
                        .filter(([k]) => k !== "event_hash")
                        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                        .join("  ")}
                    </p>
                  )}
                  <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: "0.6rem", color: "var(--z-ink-3)", opacity: 0.65, overflowWrap: "anywhere" }}>
                    {e.event_hash.slice(0, 40)}…
                  </p>
                </li>
              ))}
            </ol>
          </ZPanel>
        </div>
      )}
    </div>
  );
}
