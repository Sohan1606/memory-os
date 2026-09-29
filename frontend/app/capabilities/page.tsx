"use client";
/**
 * ZORQ Capability visibility surface (Z-UI.1 WP-UI-5).
 * Sealed registry contents with the five-level ladder. A capability shown
 * here grants nothing: visibility is not permission, and no item is
 * executable from this surface.
 */
import {
  ErrorPanel, LadderChips, LoadingPanel, MetaBadge, PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { zorqApi } from "@/lib/api";

/**
 * The five-level capability ladder is a contract, not live data — the same
 * model the sealed registry exposes through the facade. It renders
 * regardless of facade availability; the facade's own copy is preferred
 * whenever the registry is reachable.
 */
const LADDER_MODEL = [
  { level: "VISIBLE", meaning: "Rendered in the interface (grants nothing)." },
  { level: "AVAILABLE", meaning: "Verified implementation operable in the current device + connection context." },
  { level: "AUTHORIZED", meaning: "Current backend authorization state permits it for this principal now." },
  { level: "EXECUTABLE", meaning: "AVAILABLE + AUTHORIZED + preconditions satisfied (dispatchable now)." },
  { level: "VERIFIED OUTCOME", meaning: "Evidence-established result of an executed action (not a capability state)." },
];

const VISIBILITY_NOTE = "A capability shown here grants nothing. Visibility is not permission; execution requires the full Action Plane flow (authorization → snapshot → lease → execution → verification → audit).";

export default function CapabilitiesPage() {
  const caps = useFacade(zorqApi.capabilities, 30_000);
  const data = caps.data?.available ? caps.data : null;

  return (
    <div className="z-page">
      <PageHeader
        kicker="System · Capabilities"
        title="Capabilities"
        lede="What ZORQ can do — and, separately, what it is currently permitted to do. These are the sealed registry contents of the installed action plane, rendered read-only."
        right={<MetaBadge note="The capability registry is sealed at composition; nothing can be installed at runtime.">SEALED</MetaBadge>}
      />

      <ZPanel
        title="Visibility is not permission"
        hint={data?.visibility_note ?? VISIBILITY_NOTE}
        style={{ marginBottom: "1.2rem" }}
      >
        <div style={{ display: "grid", gap: "0.5rem", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))" }}>
          {(data?.ladder_model ?? LADDER_MODEL).map((level, i) => (
            <div key={level.level} style={{ border: "1px solid var(--z-line)", borderRadius: 6, padding: "0.7rem 0.85rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.3rem" }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: "0.6rem", color: "var(--z-accent)", opacity: 0.8 }}>{i + 1}</span>
                <StateToken
                  value={level.level}
                  note={level.meaning}
                />
              </div>
              <p style={{ margin: 0, fontSize: "0.74rem", color: "var(--z-ink-3)", lineHeight: 1.5 }}>{level.meaning}</p>
            </div>
          ))}
        </div>
      </ZPanel>

      {caps.condition === "LOADING" && <LoadingPanel rows={5} />}
      {caps.condition === "ERROR" && <ErrorPanel retry={caps.refresh} />}
      {caps.condition === "UNAVAILABLE" && caps.reason && <UnavailablePanel reason={caps.reason} retry={caps.refresh} />}

      {data && (
        <>
          <div className="z-grid" data-cols="2">
            {data.capabilities.map((cap) => (
              <ZPanel
                key={cap.capability_id}
                title={cap.capability_id}
                right={<StateToken value={cap.risk} title="Risk class" />}
                hint={cap.description}
              >
                <LadderChips
                  visible={cap.ladder.visible}
                  available={cap.ladder.available}
                  authorized={cap.ladder.authorized}
                  executable={cap.ladder.executable}
                />
                <div style={{ marginTop: "0.9rem" }}>
                  <Readout k="Version" muted>{cap.version}</Readout>
                  <Readout k="Confirmation"><StateToken value={cap.confirmation_mode === "NONE" ? "NONE REQUIRED" : cap.confirmation_mode} note={cap.confirmation_mode === "EXPLICIT" ? "Explicit owner confirmation is required before execution." : "No confirmation gate for this read-only capability."} /></Readout>
                  <Readout k="Memory governance" muted>{cap.requires_memory_governance ? "REQUIRED" : "NOT REQUIRED"}</Readout>
                  <Readout k="Verification" muted>{cap.verification}</Readout>
                  <Readout k="Cancellation" muted>{cap.cancellation}</Readout>
                  <Readout k="Timeout" muted>{cap.timeout_seconds}s</Readout>
                  <Readout k="Operations" muted>{cap.operations.join(" · ")}</Readout>
                  <Readout k="Permissions" muted>{cap.permissions.join(" · ")}</Readout>
                </div>
              </ZPanel>
            ))}
          </div>
          <p style={{ fontSize: "0.72rem", color: "var(--z-ink-3)", margin: "1rem 0 0", fontFamily: "var(--mono)" }}>
            registry digest {data.registry_digest.slice(0, 24)}…
          </p>
        </>
      )}
    </div>
  );
}
