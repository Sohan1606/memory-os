"use client";
/**
 * ZORQ Actions & Verification surface (Z-UI.1 WP-UI-5).
 * Read-only, authorization-aware. The lifecycle contract renders from the
 * facade; action records appear only when they are real. The surface exists
 * to make two things obvious: authorization is a boundary, and EXECUTED is
 * not VERIFIED.
 *
 * Structural semantics (the lifecycle vocabulary, the status vocabulary and
 * its COMPLETED/VERIFIED distinction, the authority invariants) are part of
 * the contract, not live data — they render regardless of facade
 * availability. Live records and runtime values render only from real
 * facade data, with honest unavailable states otherwise.
 */
import {
  ACTION_LIFECYCLE, ErrorPanel, LoadingPanel, LifecycleStepper, MetaBadge,
  PageHeader, Readout, UnavailablePanel, ZPanel,
} from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { useFacade } from "@/components/zorq/useFacade";
import { zorqApi } from "@/lib/api";

const STATUS_SEMANTICS = [
  { status: "COMPLETED", meaning: "Execution finished. Says nothing about correctness." },
  { status: "VERIFIED", meaning: "Evidence established the outcome. COMPLETED never implies this." },
  { status: "UNKNOWN", meaning: "Outcome could not be determined. Never rendered as success." },
  { status: "DENIED", meaning: "Authorization refused. Refusal is a first-class result, not an error." },
  { status: "STOPPED", meaning: "Halted by emergency stop or cancellation." },
];

const INVARIANTS = [
  "UI VISIBILITY ≠ AUTHORIZATION",
  "UI INTENT ≠ AUTHORIZATION",
  "DISPLAYED CAPABILITY ≠ PERMISSION",
  "MODEL CONFIDENCE ≠ AUTHORIZATION",
  "EXECUTION ≠ VERIFIED OUTCOME",
];

export default function ActionsPage() {
  const actions = useFacade(zorqApi.actions, 15_000);
  const data = actions.data?.available ? actions.data : null;

  return (
    <div className="z-page">
      <PageHeader
        kicker="System · Actions"
        title="Actions & verification"
        lede="Every real action travels one path: proposal, authorization, an immutable snapshot, a one-use lease, device execution, verification against evidence, and audit. The interface offers no shortcut — and no one-click irreversible action."
        right={<MetaBadge note="This surface presents the lifecycle contract; it initiates nothing. The facade is GET-only.">READ-ONLY</MetaBadge>}
      />

      <div className="z-grid" data-cols="2">
        <ZPanel title="Lifecycle" hint="The contract every action follows. None is currently in flight — this is the model, not a simulation." wide>
          <LifecycleStepper phases={ACTION_LIFECYCLE} />
          {actions.condition === "LOADING" && <LoadingPanel />}
          {actions.condition === "ERROR" && <ErrorPanel retry={actions.refresh} />}
          {actions.condition === "UNAVAILABLE" && actions.reason && <UnavailablePanel reason={actions.reason} retry={actions.refresh} />}
          {data && (
            <div style={{ marginTop: "1.1rem", display: "grid", gap: "0.45rem" }}>
              {data.lifecycle.map((step) => (
                <p key={step.phase} style={{ margin: 0, fontSize: "0.78rem", color: "var(--z-ink-3)", lineHeight: 1.5 }}>
                  <span style={{ fontFamily: "var(--mono)", color: "var(--z-accent)", fontSize: "0.68rem", letterSpacing: "0.08em" }}>{step.phase}</span>
                  {" — "}{step.meaning}
                </p>
              ))}
            </div>
          )}
        </ZPanel>

        <ZPanel
          title="Action records"
          hint={data?.records_note
            ?? "The action runtime is not yet exposed over HTTP, so no action can occur through this system."}
        >
          <div className="z-condition" role="note">
            <span className="z-condition-label">No action records</span>
            <p className="z-condition-note">
              This is the truthful state: the action runtime is not yet exposed over HTTP,
              so no actions have occurred through this system. When action surfaces ship,
              real records — with their status, verification evidence and audit references —
              appear here.
            </p>
          </div>
        </ZPanel>

        <ZPanel title="Verification semantics" hint="COMPLETED and VERIFIED are never conflated, in meaning or in appearance.">
          <div style={{ display: "grid", gap: "0.55rem" }}>
            {STATUS_SEMANTICS.map((s) => (
              <div key={s.status} style={{ display: "flex", gap: "0.8rem", alignItems: "center", flexWrap: "wrap" }}>
                <StateToken value={s.status} />
                <span style={{ fontSize: "0.78rem", color: "var(--z-ink-3)", flex: 1, minWidth: "12rem" }}>{s.meaning}</span>
              </div>
            ))}
          </div>
        </ZPanel>

        {data && (
          <>
            <ZPanel title="Execution origin" hint="Where an action executes is explicit, never implied.">
              <Readout k="LOCAL" muted>{data.execution_origins.LOCAL === "SUPPORTED" ? "Supported — this device" : data.execution_origins.LOCAL}</Readout>
              <Readout k="REMOTE"><StateToken value={data.execution_origins.REMOTE} /></Readout>
              <Readout k="DELEGATED"><StateToken value={data.execution_origins.DELEGATED} note="Authorized delegation with explicit scope and expiry — B-32." /></Readout>
              <p style={{ fontSize: "0.75rem", color: "var(--z-ink-3)", margin: "0.7rem 0 0", lineHeight: 1.5 }}>
                {data.one_click_prohibition}
              </p>
            </ZPanel>

            <ZPanel title="Status vocabulary" hint="The complete ActionStatus vocabulary the backend can produce." wide>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                {data.status_vocabulary.map((s) => <StateToken key={s} value={s} small />)}
              </div>
            </ZPanel>
          </>
        )}

        <ZPanel title="Authority boundaries" hint="Standing invariants of this system — enforced in the backend, reflected here." wide>
          <div style={{ display: "grid", gap: "0.55rem", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))" }}>
            {INVARIANTS.map((inv) => (
              <p key={inv} style={{
                margin: 0, fontFamily: "var(--mono)", fontSize: "0.66rem",
                letterSpacing: "0.1em", color: "var(--z-ink-2)",
                border: "1px solid var(--z-line)", borderRadius: 4, padding: "0.6rem 0.75rem",
              }}>
                {inv}
              </p>
            ))}
          </div>
        </ZPanel>
      </div>
    </div>
  );
}
