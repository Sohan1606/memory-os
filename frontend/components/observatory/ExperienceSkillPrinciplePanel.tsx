"use client";
/** V8.4.1 — real Experience -> Skill -> Principle learning, deeply inspectable. */
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type {
  Experience, KnowledgeExplanation, LearnedKnowledge,
} from "@/lib/types";
import { Empty, Meter, Panel, StateBadge } from "./primitives";

const heading = {
  fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
  textTransform: "uppercase" as const, color: "var(--silver)", margin: 0,
};

export default function ExperienceSkillPrinciplePanel(
  { refreshKey = 0 }: { refreshKey?: number },
) {
  const [experiences, setExperiences] = useState<Experience[] | null>(null);
  const [skills, setSkills] = useState<LearnedKnowledge[] | null>(null);
  const [principles, setPrinciples] = useState<LearnedKnowledge[] | null>(null);
  const [selected, setSelected] = useState<KnowledgeExplanation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.experiences(), api.skills(), api.principles()])
      .then(([e, s, p]) => {
        setExperiences(e.experiences);
        setSkills(s.skills);
        setPrinciples(p.principles);
        setError(null);
      })
      .catch((e) => setError(
        e instanceof Error ? e.message : "Learned knowledge is unavailable.",
      ));
  }, []);

  useEffect(load, [load, refreshKey]);

  const counts = useMemo(() => ({
    experiences: experiences?.length ?? 0,
    skills: skills?.length ?? 0,
    principles: principles?.length ?? 0,
  }), [experiences, skills, principles]);

  async function inspect(item: LearnedKnowledge) {
    try {
      const detail = await api.learnedExplanation(item.kind, item.id);
      setSelected(detail);
      await api.setFocus(item.kind, item.id, { label: item.name });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not inspect learned knowledge.");
    }
  }

  const loading = experiences === null || skills === null || principles === null;

  return (
    <Panel
      title="Experience → Skill → Principle"
      hint="Confidence is evidence support. Reputation is performance after actual use. Candidates are never silently promoted."
      style={{ gridColumn: "1 / -1" }}
      right={<span style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
        <StateBadge value={`${counts.experiences} EXPERIENCE${counts.experiences === 1 ? "" : "S"}`} />
        <StateBadge value={`${counts.skills} SKILL${counts.skills === 1 ? "" : "S"}`} />
        <StateBadge value={`${counts.principles} PRINCIPLE${counts.principles === 1 ? "" : "S"}`} />
      </span>}
    >
      {error && <div style={{ marginBottom: "0.9rem" }}><Empty>{error}</Empty></div>}
      {loading ? <Empty>Loading evidence-backed learning…</Empty> : (
        <div style={{
          display: "grid", gap: "1.2rem",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
        }}>
          <section>
            <h4 style={heading}>Experiences</h4>
            <p style={{ color: "var(--muted)", fontSize: "0.72rem", lineHeight: 1.5,
                        margin: "0.3rem 0 0.65rem" }}>
              Meaningful episodes traced to canonical observations.
            </p>
            {experiences?.length ? experiences.slice(0, 6).map((item) => (
              <article key={item.id} style={{
                borderTop: "1px solid var(--line)", padding: "0.65rem 0",
                minWidth: 0,
              }}>
                <div style={{ display: "flex", gap: "0.4rem", alignItems: "center",
                              justifyContent: "space-between" }}>
                  <StateBadge value={item.lifecycle} />
                  <span style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                                 color: "var(--muted)" }}>
                    {item.evidence_count} evidence
                  </span>
                </div>
                <p style={{ color: "var(--warm)", fontSize: "0.82rem", lineHeight: 1.5,
                            margin: "0.45rem 0 0", overflowWrap: "anywhere" }}>
                  {item.situation}
                </p>
                {item.outcome && <p style={{ color: "var(--muted)", fontSize: "0.72rem",
                                            lineHeight: 1.45, margin: "0.25rem 0 0" }}>
                  Outcome: {item.outcome} {item.success === null ? "" : item.success ? "✓" : "✕"}
                </p>}
              </article>
            )) : <Empty>No evidence-backed experiences recorded yet.</Empty>}
          </section>

          <KnowledgeColumn title="Skills" subtitle="Operational procedures that can influence decisions."
                           items={skills ?? []} onInspect={inspect} />
          <KnowledgeColumn title="Principles" subtitle="Higher-order guidance supported across skills."
                           items={principles ?? []} onInspect={inspect} />
        </div>
      )}

      {selected && <section style={{
        borderTop: "1px solid var(--line-strong)", marginTop: "1.2rem",
        paddingTop: "1rem",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.55rem",
                      justifyContent: "space-between", flexWrap: "wrap" }}>
          <h4 style={heading}>Evidence &amp; provenance · {selected.item.name}</h4>
          <button type="button" onClick={() => setSelected(null)} style={{
            border: 0, background: "transparent", color: "var(--muted)",
            fontFamily: "var(--mono)", fontSize: "0.65rem", cursor: "pointer",
          }}>Close</button>
        </div>
        <p style={{ color: "var(--warm)", fontSize: "0.82rem", lineHeight: 1.6,
                    margin: "0.65rem 0" }}>{selected.summary}</p>
        <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
          <StateBadge value={selected.item.lifecycle} />
          <StateBadge value={selected.item.reputation.reputation} />
          <StateBadge value={`${selected.supporting_evidence.length} SUPPORTING`} />
          <StateBadge value={`${selected.counterexamples.length} COUNTEREXAMPLES`} />
          <StateBadge value={`${selected.usage_history.length} USES`} />
        </div>
        {selected.validation_history[0] && <p style={{
          color: "var(--muted)", fontSize: "0.72rem", lineHeight: 1.55,
          margin: "0.65rem 0 0",
        }}>
          Latest validation: {selected.validation_history[0].decision} — {selected.validation_history[0].reason}
        </p>}
        <p style={{ color: "var(--muted)", fontSize: "0.68rem", margin: "0.45rem 0 0" }}>
          {selected.note}
        </p>
      </section>}
    </Panel>
  );
}

function KnowledgeColumn({ title, subtitle, items, onInspect }: {
  title: string; subtitle: string; items: LearnedKnowledge[];
  onInspect: (item: LearnedKnowledge) => void;
}) {
  return <section>
    <h4 style={heading}>{title}</h4>
    <p style={{ color: "var(--muted)", fontSize: "0.72rem", lineHeight: 1.5,
                margin: "0.3rem 0 0.65rem" }}>{subtitle}</p>
    {items.length ? items.slice(0, 6).map((item) => (
      <button key={item.id} type="button" onClick={() => void onInspect(item)} style={{
        display: "block", width: "100%", textAlign: "left", color: "inherit",
        background: "transparent", border: 0, borderTop: "1px solid var(--line)",
        padding: "0.65rem 0", cursor: "pointer", minWidth: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem",
                      justifyContent: "space-between", flexWrap: "wrap" }}>
          <StateBadge value={item.lifecycle} />
          <StateBadge value={item.reputation.reputation} />
        </div>
        <p style={{ color: "var(--warm)", fontSize: "0.82rem", lineHeight: 1.5,
                    margin: "0.45rem 0", overflowWrap: "anywhere" }}>{item.name}</p>
        <div style={{ display: "flex", gap: "0.7rem", alignItems: "center",
                      justifyContent: "space-between", flexWrap: "wrap" }}>
          <Meter value={item.confidence} label={`confidence ${Math.round(item.confidence * 100)}%`} />
          <span style={{ fontFamily: "var(--mono)", fontSize: "0.6rem",
                         color: "var(--muted)" }}>
            {item.scope_kind}{item.scope_value ? ` · ${item.scope_value}` : ""}
          </span>
        </div>
        <p style={{ fontFamily: "var(--mono)", color: "var(--muted)",
                    fontSize: "0.62rem", margin: "0.4rem 0 0" }}>
          {item.supporting_evidence_count} supporting · {item.counterexample_count} counter · {item.usages.length} uses
        </p>
      </button>
    )) : <Empty>No {title.toLowerCase()} recorded yet.</Empty>}
  </section>;
}
