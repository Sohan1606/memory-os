"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Mission, MissionBrief } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

/**
 * V8.3 §10–§12 — long-running missions.
 *
 * This is an inspection surface, not a task manager: missions are created and
 * driven through conversation. What the panel adds is visibility into state,
 * why a mission is blocked, and which ones have quietly gone stale.
 */
export default function MissionPanel({ refreshKey = 0 }:
  { refreshKey?: number }) {
  const [missions, setMissions] = useState<Mission[] | null>(null);
  const [brief, setBrief] = useState<MissionBrief | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, b] = await Promise.all([api.missions(true), api.missionBrief()]);
      setMissions(m.missions);
      setBrief(b.brief);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  if (error) return <Panel title="Missions"><Empty>{error}</Empty></Panel>;
  if (!missions || !brief) {
    return <Panel title="Missions"><Empty>Loading…</Empty></Panel>;
  }

  return (
    <Panel
      title="Missions"
      hint="Objectives that outlive a single conversation. Progress only moves
            when a step is actually completed."
      right={<StateBadge value={brief.summary.toUpperCase()} />}
    >
      {missions.length === 0 ? (
        <Empty>No open missions. Mention a long-running objective in
               conversation and it will be tracked here.</Empty>
      ) : missions.slice(0, 6).map((mission) => (
        <div key={mission.id} style={{ padding: "0.65rem 0",
                                       borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        gap: "1rem", alignItems: "flex-start" }}>
            <p style={{ fontSize: "0.82rem", color: "var(--warm)", margin: 0,
                        lineHeight: 1.5 }}>{mission.title}</p>
            <StateBadge value={mission.state.toUpperCase()} />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem",
                        margin: "0.45rem 0 0" }}>
            <div style={{ flex: 1, height: 3, background: "var(--line)",
                          borderRadius: 2, overflow: "hidden" }}>
              <div style={{ width: `${Math.round(mission.progress * 100)}%`,
                            height: "100%", background: "var(--silver)" }} />
            </div>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                           color: "var(--muted)" }}>
              {Math.round(mission.progress * 100)}%
            </span>
          </div>

          {mission.blocked_reason && (
            <p style={{ fontSize: "0.74rem", color: "var(--muted)",
                        margin: "0.35rem 0 0", lineHeight: 1.5 }}>
              Blocked: {mission.blocked_reason}
            </p>
          )}
          {mission.waiting_on && (
            <p style={{ fontSize: "0.74rem", color: "var(--muted)",
                        margin: "0.35rem 0 0", lineHeight: 1.5 }}>
              Waiting on: {mission.waiting_on}
            </p>
          )}
          {mission.steps.length > 0 && (
            <p style={{ fontFamily: "var(--mono)", fontSize: "0.64rem",
                        color: "var(--muted)", margin: "0.35rem 0 0" }}>
              {mission.steps.filter((s) => s.state === "done").length}
              {" / "}{mission.steps.length} step(s) done
            </p>
          )}
        </div>
      ))}

      {brief.needs_review.length > 0 && (
        <>
          <h4 style={headingStyle}>Gone quiet</h4>
          {brief.needs_review.slice(0, 4).map((item) => (
            <p key={item.id} style={{ fontSize: "0.74rem",
                                      color: "var(--muted)",
                                      margin: "0.3rem 0", lineHeight: 1.5 }}>
              {item.title} — {item.review_reason}
            </p>
          ))}
        </>
      )}
    </Panel>
  );
}

const headingStyle = {
  fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
  textTransform: "uppercase" as const, color: "var(--silver)",
  margin: "1.3rem 0 0.5rem",
};
