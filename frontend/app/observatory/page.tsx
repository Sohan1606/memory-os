"use client";
/**
 * The Observatory: one unified environment for inspecting the system's mind.
 *
 * Progressive disclosure - the primary reading is human-readable; Developer mode
 * reveals raw event types, ids and correlation ids.
 */
import { useCallback, useEffect, useState } from "react";

import ActivityFeed from "@/components/observatory/ActivityFeed";
import AutonomyPanel from "@/components/observatory/AutonomyPanel";
import BackgroundPanel from "@/components/observatory/BackgroundPanel";
import CapabilityPanel from "@/components/observatory/CapabilityPanel";
import CognitivePolicyPanel from "@/components/observatory/CognitivePolicyPanel";
import ContinuityPanel from "@/components/observatory/ContinuityPanel";
import ContinuousStatePanel from "@/components/observatory/ContinuousStatePanel";
import InfluencePanel from "@/components/observatory/InfluencePanel";
import ExperienceSkillPrinciplePanel from "@/components/observatory/ExperienceSkillPrinciplePanel";
import MissionPanel from "@/components/observatory/MissionPanel";
import LearningPanel from "@/components/observatory/LearningPanel";
import MemoryHealthPanel from "@/components/observatory/MemoryHealthPanel";
import ProviderPanel from "@/components/observatory/ProviderPanel";
import PortabilityPanel from "@/components/observatory/PortabilityPanel";
import ResearchPanel from "@/components/observatory/ResearchPanel";
import SandboxPanel from "@/components/observatory/SandboxPanel";
import SecurityPanel from "@/components/observatory/SecurityPanel";
import SelfPanel from "@/components/observatory/SelfPanel";
import SystemHealthPanel from "@/components/observatory/SystemHealthPanel";
import WhyInspector from "@/components/observatory/WhyInspector";
import WorldPanel from "@/components/observatory/WorldPanel";
import { Empty, Panel, Row, StateBadge } from "@/components/observatory/primitives";
import { api } from "@/lib/api";
import type { CognitionStatus, ResumeBriefing } from "@/lib/types";

export default function ObservatoryPage() {
  const [status, setStatus] = useState<CognitionStatus | null>(null);
  const [resume, setResume] = useState<ResumeBriefing | null>(null);
  const [developer, setDeveloper] = useState(false);
  const [subject, setSubject] = useState<{ kind: string; id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => {
    api.cognitionStatus()
      .then((s) => { setStatus(s); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : "Observatory unavailable."));
    api.resume().then(setResume).catch(() => setResume(null));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(() => { load(); setRefreshKey((k) => k + 1); }, 8000);
    return () => window.clearInterval(id);
  }, [load]);

  const eventTotal = status
    ? Object.values(status.events).reduce((a, b) => a + b, 0) : 0;

  return (
    <main id="main" style={{ padding: "7rem var(--pad) 6rem", maxWidth: "var(--maxw)",
                             margin: "0 auto" }}>
      <header style={{ marginBottom: "2.5rem" }}>
        <p className="label" style={{ color: "var(--accent)" }}>Observatory</p>
        <h1 className="headline" style={{ marginTop: "0.8rem", maxWidth: "18ch" }}>
          The system, inspectable.
        </h1>
        <p className="body body-lg" style={{ marginTop: "1.2rem", maxWidth: "62ch" }}>
          Everything below is derived from recorded evidence. Where the system has
          not measured something, it says so rather than estimating.
        </p>

        <div style={{ display: "flex", gap: "0.6rem", alignItems: "center",
                      marginTop: "1.6rem", flexWrap: "wrap" }}>
          <StateBadge value={`${eventTotal} EVENTS`} />
          {status && <StateBadge value={status.autonomy.level.replace("_", " ")} />}
          {status && <StateBadge value={`${status.world.total} TRACKED`} />}
          <label style={{
            display: "inline-flex", alignItems: "center", gap: "0.45rem",
            fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.09em",
            textTransform: "uppercase", color: "var(--silver)", cursor: "pointer",
            marginLeft: "auto",
          }}>
            <input
              type="checkbox"
              checked={developer}
              onChange={(e) => setDeveloper(e.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            Developer mode
          </label>
        </div>
      </header>

      {error && (
        <div style={{
          border: "1px solid rgba(232,138,122,0.32)", borderRadius: 4,
          padding: "1rem", marginBottom: "2rem",
        }}>
          <p style={{ fontSize: "0.85rem", color: "#e88a7a", margin: 0 }}>
            {error} The backend may not be running — start it and this page will recover.
          </p>
        </div>
      )}

      {resume && (
        <Panel title="Where we left off" style={{ marginBottom: "1.5rem" }}>
          <p style={{ fontSize: "0.9rem", color: "var(--warm)", lineHeight: 1.6, margin: 0 }}>
            {resume.briefing}
          </p>
        </Panel>
      )}

      <div style={{
        display: "grid", gap: "1.5rem",
        gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
      }}>
        <SecurityPanel refreshKey={refreshKey} />
        <SystemHealthPanel refreshKey={refreshKey} />
        <MissionPanel refreshKey={refreshKey} />
        <WorldPanel refreshKey={refreshKey} />
        <ExperienceSkillPrinciplePanel refreshKey={refreshKey} />
        <ContinuousStatePanel refreshKey={refreshKey} />
        <BackgroundPanel refreshKey={refreshKey} />
        <ActivityFeed
          developer={developer}
          onSelect={(e) => {
            if (e.subject_kind && e.subject_id) {
              setSubject({ kind: e.subject_kind, id: e.subject_id });
            }
          }}
        />
        <ProviderPanel />
        <CapabilityPanel />
        <MemoryHealthPanel />
        <InfluencePanel refreshKey={refreshKey} />
        <CognitivePolicyPanel refreshKey={refreshKey} />
        <ContinuityPanel refreshKey={refreshKey} />
        <AutonomyPanel refreshKey={refreshKey} />
        <SelfPanel />
        <LearningPanel refreshKey={refreshKey} />
        <SandboxPanel />
        <ResearchPanel refreshKey={refreshKey} />
        <PortabilityPanel refreshKey={refreshKey} />

        <Panel title="Predictions" hint="Calibration is only reported once enough predictions have resolved.">
          {status ? (
            <>
              <Row label="Calibration" value={<StateBadge value={status.predictions.calibration} />} />
              <Row label="Resolved" value={status.predictions.resolved} />
              <Row label="Brier score" value={status.predictions.brier ?? "—"} />
            </>
          ) : <Empty>Loading…</Empty>}
        </Panel>

        <Panel title="Intent" hint="What the system currently believes you are trying to do.">
          {status?.intent ? (
            <>
              <p style={{ fontSize: "0.88rem", color: "var(--warm)", lineHeight: 1.6,
                          margin: "0 0 0.6rem" }}>{status.intent.label}</p>
              <StateBadge value={status.intent.status} />
            </>
          ) : <Empty>No current intent detected. Tell the assistant what you are working on.</Empty>}
        </Panel>

        {subject && (
          <WhyInspector subject={subject} onClose={() => setSubject(null)} />
        )}
      </div>

      {developer && status && (
        <Panel title="Event counts (developer)" style={{ marginTop: "1.5rem" }}>
          <div style={{ display: "grid", gap: "0 1.5rem",
                        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
            {Object.entries(status.events).sort().map(([type, n]) => (
              <Row key={type} label={type} value={n} />
            ))}
          </div>
        </Panel>
      )}
    </main>
  );
}
