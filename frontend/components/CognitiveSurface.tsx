"use client";

import type { CognitiveSurfaceState, LiveSurfaceState } from "@/lib/types";

const LABELS: Record<string, string> = {
  UNDERSTANDING: "Understanding",
  CHECKING_PERSONAL_CONTEXT: "Checking personal context",
  CHECKING_MEMORY: "Checking memory",
  CHECKING_WORLD_STATE: "Checking world state",
  VERIFYING_EXTERNAL_INFORMATION: "Verifying external information",
  IDENTIFYING_UNKNOWNS: "Identifying unknowns",
  EVALUATING_CONSEQUENCES: "Evaluating consequences",
  FORMING_RESPONSE: "Forming response",
  WAITING_FOR_USER: "Waiting for you",
  UPDATING_MODEL: "Updating personal model",
  // V10.1: real maintenance stages, emitted only when the work executed.
  AUDITING_PERSONAL_MODEL: "Auditing personal model",
  CHECKING_FOR_STALE_STATE: "Checking for stale state",
  COMPARING_PERSONAL_STATE: "Comparing personal state",
  CHECKING_CONTRADICTIONS: "Checking contradictions",
  CHECKING_OPEN_UNKNOWNS: "Checking open unknowns",
  COMPARING_PREDICTION_TO_OUTCOME: "Comparing prediction to outcome",
  ANALYZING_MODEL_ERROR: "Analyzing model error",
  EVALUATING_MAINTENANCE_OPTIONS: "Evaluating maintenance options",
  WAITING_FOR_CONFIRMATION: "Waiting for your confirmation",
  APPLYING_VERIFIED_UPDATE: "Applying your verified update",
  RE_AUDITING_MODEL: "Re-auditing personal model",
  // V10.2: adaptive-policy governance stages, emitted only when the work ran.
  CHECKING_ADAPTIVE_POLICY: "Checking adaptive policy",
  COMPARING_POLICY_EVIDENCE: "Comparing policy evidence",
  EVALUATING_POLICY_CHANGE: "Evaluating a policy change",
  WAITING_FOR_POLICY_CONFIRMATION: "Waiting for your policy decision",
  APPLYING_ADAPTIVE_POLICY: "Applying your confirmed policy",
  MEASURING_POLICY_OUTCOME: "Measuring a policy outcome",
};

export default function CognitiveSurface({ surface, live = false }: {
  surface: CognitiveSurfaceState | LiveSurfaceState; live?: boolean;
}) {
  const completed = "system_state" in surface;
  const systemStatus = completed ? surface.system_state.status : surface.status;
  const activeObjects = completed ? surface.active_objects : [];
  const uncertainties = completed ? surface.uncertainties : [];
  return (
    <section aria-label="Cognitive activity" style={{ display: "grid", gap: "0.9rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "center" }}>
        <p className="label label-accent">{live ? "Live cognitive activity" : "Cognitive surface"}</p>
        <span className="chip" data-active={systemStatus === "ACTIVE"}>
          {systemStatus}
        </span>
      </div>

      <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "0.45rem" }}>
        {surface.activities.map((activity, index) => (
          <li key={`${activity.stage}-${index}`} style={{
            display: "grid", gridTemplateColumns: "10px minmax(0,1fr) auto",
            alignItems: "center", gap: "0.65rem", color: "var(--silver)",
          }} title={`${activity.source.kind}: ${activity.source.id}`}>
            <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%",
              background: activity.status === "DEGRADED" ? "#e8a07a" : "var(--accent)" }} />
            <span className="body" style={{ fontSize: "0.8rem" }}>
              {LABELS[activity.stage] ?? activity.stage.replaceAll("_", " ").toLowerCase()}
            </span>
            <span className="mono" style={{ fontSize: "0.58rem", color: "var(--muted)" }}>
              {activity.status}
            </span>
          </li>
        ))}
      </ol>

      {activeObjects.length > 0 && (
        <div>
          <p className="label">Personal state changed</p>
          <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginTop: "0.45rem" }}>
            {activeObjects.map((object) => (
              <span className="chip" key={object.id} title={object.id}>{object.type}</span>
            ))}
          </div>
        </div>
      )}

      {uncertainties.length > 0 && (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: "0.7rem" }}>
          {uncertainties.map((item, index) => (
            <p className="body" key={`${item.kind}-${index}`} style={{ margin: index ? "0.4rem 0 0" : 0,
              fontSize: "0.76rem", color: "#e8b48a" }}>
              <span className="mono">{item.kind.replaceAll("_", " ")}</span> · {item.detail}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
