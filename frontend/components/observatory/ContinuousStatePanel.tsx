"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  ConnectorStatus, ObservationStats, SilencePolicy, Suppression, WorldSnapshot,
} from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

/**
 * V8.3 §9 / §15–§17 / §26 — the continuous state of the system.
 *
 * Four honest readings in one surface:
 *   · world freshness — stale facts keep their value, they are just due a check
 *   · evidence        — observations, split by epistemic status
 *   · silence         — what was deliberately not raised, and why
 *   · connectors      — NOT CONNECTED, stated plainly
 */
export default function ContinuousStatePanel({ refreshKey = 0 }:
  { refreshKey?: number }) {
  const [world, setWorld] = useState<WorldSnapshot | null>(null);
  const [stats, setStats] = useState<ObservationStats | null>(null);
  const [policy, setPolicy] = useState<SilencePolicy | null>(null);
  const [suppressions, setSuppressions] = useState<Suppression[] | null>(null);
  const [connectors, setConnectors] = useState<ConnectorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [w, o, p, s, c] = await Promise.all([
        api.worldSnapshot(), api.observations(), api.attentionPolicy(),
        api.suppressions(), api.connectors(),
      ]);
      setWorld(w);
      setStats(o.stats);
      setPolicy(p);
      setSuppressions(s.suppressions);
      setConnectors(c);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  if (error) {
    return <Panel title="Continuous state"><Empty>{error}</Empty></Panel>;
  }
  if (!world || !stats || !policy || !suppressions || !connectors) {
    return <Panel title="Continuous state"><Empty>Loading…</Empty></Panel>;
  }

  return (
    <Panel
      title="Continuous state"
      hint="What the system currently holds, how fresh it is, and what it chose
            not to say."
      right={<StateBadge value={`${world.count} FACTS`} />}
    >
      <h4 style={headingStyle}>World freshness</h4>
      <p style={bodyStyle}>{world.detail}</p>
      {world.stale_count > 0 && (
        <p style={bodyStyle}>
          A stale fact keeps its recorded value — it is flagged for
          re-confirmation, not treated as false.
        </p>
      )}
      {world.entities.slice(0, 5).map((fact) => (
        <div key={fact.id} style={{ display: "flex",
                                    justifyContent: "space-between",
                                    gap: "1rem", padding: "0.35rem 0" }}>
          <span style={{ fontSize: "0.76rem", color: "var(--warm)",
                         overflow: "hidden", textOverflow: "ellipsis",
                         whiteSpace: "nowrap" }}>
            {fact.label}
          </span>
          <StateBadge value={fact.freshness.freshness_class} />
        </div>
      ))}

      <h4 style={headingStyle}>Evidence recorded</h4>
      <p style={bodyStyle}>{stats.detail}</p>
      {Object.entries(stats.by_status).length > 0 && (
        <p style={monoStyle}>
          {Object.entries(stats.by_status)
            .map(([k, v]) => `${k} ${v}`).join("  ·  ")}
        </p>
      )}

      <h4 style={headingStyle}>Silence</h4>
      <p style={bodyStyle}>{policy.detail}</p>
      {suppressions.length === 0 ? (
        <Empty>Nothing has been suppressed yet.</Empty>
      ) : suppressions.slice(0, 4).map((item) => (
        <div key={item.id} style={{ padding: "0.4rem 0" }}>
          <p style={{ ...bodyStyle, margin: 0 }}>{item.topic}</p>
          <p style={{ ...monoStyle, margin: "0.2rem 0 0" }}>
            {item.suppressed_because}
          </p>
        </div>
      ))}

      <h4 style={headingStyle}>Connectors</h4>
      <p style={bodyStyle}>{connectors.detail}</p>
      <p style={monoStyle}>
        {connectors.connectors.map((c) => `${c.name.toUpperCase()} ${c.state}`)
          .join("  ·  ")}
      </p>
    </Panel>
  );
}

const headingStyle = {
  fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
  textTransform: "uppercase" as const, color: "var(--silver)",
  margin: "1.2rem 0 0.5rem",
};

const bodyStyle = {
  fontSize: "0.77rem", color: "var(--muted)", margin: "0 0 0.4rem",
  lineHeight: 1.55,
};

const monoStyle = {
  fontFamily: "var(--mono)", fontSize: "0.63rem", color: "var(--muted)",
  margin: "0 0 0.4rem", lineHeight: 1.6,
};
