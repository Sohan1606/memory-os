"use client";
/** The living world model: what the system believes is going on in your life. */
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { WorldEntity, WorldSummary } from "@/lib/types";
import { Empty, Meter, Panel, StateBadge } from "./primitives";

const KIND_LABEL: Record<string, string> = {
  goal: "Goal", project: "Project", commitment: "Commitment", person: "Person",
  risk: "Risk", resource: "Resource", constraint: "Constraint",
};

export default function WorldPanel({ refreshKey }: { refreshKey: number }) {
  const [entities, setEntities] = useState<WorldEntity[]>([]);
  const [summary, setSummary] = useState<WorldSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.world()
      .then((d) => { if (!cancelled) { setEntities(d.entities); setSummary(d.summary); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "World unavailable."); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  return (
    <Panel
      title="World"
      hint="Built only from what you have actually said. Nothing is assumed."
      right={summary ? <StateBadge value={`${summary.total} TRACKED`} /> : null}
    >
      {error && <Empty>{error}</Empty>}
      {!error && entities.length === 0 && (
        <Empty>
          Nothing tracked yet. Mention a goal, project or commitment in conversation
          and it will appear here.
        </Empty>
      )}
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {entities.map((e) => (
          <li key={e.id} style={{
            padding: "0.7rem 0", borderBottom: "1px solid var(--line)",
          }}>
            <div style={{
              display: "flex", justifyContent: "space-between",
              gap: "0.8rem", alignItems: "baseline", flexWrap: "wrap",
            }}>
              <span style={{
                fontFamily: "var(--mono)", fontSize: "0.6rem",
                letterSpacing: "0.1em", color: "var(--muted)",
                textTransform: "uppercase",
              }}>{KIND_LABEL[e.kind] ?? e.kind}</span>
              <StateBadge value={e.state.replace("_", " ")} />
            </div>
            <p style={{
              margin: "0.3rem 0 0.45rem", fontSize: "0.86rem",
              color: "var(--warm)", lineHeight: 1.5,
            }}>{e.label}</p>
            <Meter value={e.confidence} label={`confidence ${Math.round(e.confidence * 100)}%`} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}
