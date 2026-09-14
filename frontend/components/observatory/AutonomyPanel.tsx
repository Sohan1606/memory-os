"use client";
/** Autonomy governor + evidence-based trust. Authority is never assumed. */
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Intervention, TrustScore } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

const DESCRIPTIONS: Record<string, string> = {
  observe: "Watch and remember only. Never acts.",
  assist: "Answers and suggests. Acts only on low-risk, reversible things.",
  prepare: "Also drafts and prepares work for your approval.",
  act_low_risk: "Also performs reversible changes on its own.",
  act: "Acts independently where risk is low. Irreversible work still asks.",
};

export default function AutonomyPanel({ refreshKey }: { refreshKey: number }) {
  const [level, setLevel] = useState<string>("");
  const [levels, setLevels] = useState<string[]>([]);
  const [trust, setTrust] = useState<TrustScore[]>([]);
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const [restraint, setRestraint] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.autonomy()
      .then((d) => {
        setLevel(d.level); setLevels(d.levels); setTrust(d.trust);
        setInterventions(d.interventions); setRestraint(d.precision.detail);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Autonomy unavailable."));
  }, []);

  useEffect(load, [load, refreshKey]);

  async function change(next: string) {
    setBusy(true);
    try {
      const res = await api.setAutonomy(next, "Changed from the Observatory");
      setLevel(res.level); setTrust(res.trust); setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change autonomy.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Autonomy"
      hint="How much the system may do on its own. Irreversible actions always ask, at every level."
      right={level ? <StateBadge value={level.replace("_", " ")} /> : null}
    >
      {error && <Empty>{error}</Empty>}
      <div role="group" aria-label="Autonomy level" style={{
        display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.8rem",
      }}>
        {levels.map((l) => (
          <button
            key={l}
            type="button"
            disabled={busy}
            aria-pressed={l === level}
            onClick={() => void change(l)}
            style={{
              fontFamily: "var(--mono)", fontSize: "0.62rem",
              letterSpacing: "0.08em", textTransform: "uppercase",
              padding: "0.35rem 0.6rem", borderRadius: 3, cursor: busy ? "wait" : "pointer",
              color: l === level ? "var(--black)" : "var(--silver)",
              background: l === level ? "var(--accent)" : "transparent",
              border: `1px solid ${l === level ? "var(--accent)" : "var(--line-strong)"}`,
            }}
          >{l.replace("_", " ")}</button>
        ))}
      </div>
      {level && (
        <p style={{ fontSize: "0.78rem", color: "var(--silver)", lineHeight: 1.6,
                    margin: "0 0 1rem" }}>
          {DESCRIPTIONS[level]}
        </p>
      )}

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--silver)", margin: "0 0 0.4rem",
      }}>Demonstrated reliability</h4>
      {trust.map((t) => (
        <Row key={t.capability} label={t.capability.replace("_", " ")}
             value={<StateBadge value={t.label} title={`${t.successes} succeeded / ${t.total} recorded`} />} />
      ))}

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--silver)", margin: "1.2rem 0 0.4rem",
      }}>Interruption record</h4>
      <p style={{ fontSize: "0.78rem", color: "var(--silver)", margin: "0 0 0.6rem" }}>
        {restraint || "INSUFFICIENT EVIDENCE"}
      </p>
      {interventions.length === 0 ? (
        <Empty>Nothing has been considered for interruption yet.</Empty>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0,
                     maxHeight: 200, overflowY: "auto" }}>
          {interventions.map((i) => (
            <li key={i.id} style={{
              padding: "0.5rem 0", borderBottom: "1px solid var(--line)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between",
                            gap: "0.8rem", alignItems: "baseline" }}>
                <span style={{ fontSize: "0.8rem", color: "var(--warm)" }}>{i.topic}</span>
                <StateBadge value={i.decision} />
              </div>
              <p style={{ margin: "0.2rem 0 0", fontSize: "0.7rem",
                          fontFamily: "var(--mono)", color: "var(--muted)" }}>
                {i.rationale}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
