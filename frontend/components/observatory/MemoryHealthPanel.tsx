"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { HealthFinding, MemoryHealthReport } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

const REMEDY_NOTE: Record<string, string> = {
  MERGE: "Retires the duplicate and keeps the original. Reversible.",
  REVALIDATE: "Flags the memory for confirmation. Nothing is changed.",
  DOWNGRADE: "Lowers importance. The memory stays active.",
  QUARANTINE: "Excludes it from retrieval. Reversible.",
  RETIRE: "Soft-retires it. History is preserved.",
  ARCHIVE: "Moves it out of active use. Reversible.",
};

/**
 * Diagnoses the memory store and offers non-destructive remedies.
 * Nothing is applied automatically - every action is user-initiated and
 * accompanied by the evidence that justified it.
 */
export default function MemoryHealthPanel() {
  const [report, setReport] = useState<MemoryHealthReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.memoryHealth());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const apply = async (finding: HealthFinding) => {
    setBusy(finding.memory_id);
    try {
      await api.applyHealthRemedy(finding);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply remedy");
    } finally {
      setBusy(null);
    }
  };

  if (error) return <Panel title="Memory health"><Empty>{error}</Empty></Panel>;
  if (!report) {
    return <Panel title="Memory health"><Empty>Checking memory health…</Empty></Panel>;
  }

  return (
    <Panel
      title="Memory health"
      hint="Findings are evidence-based. No memory is ever hard-deleted."
      right={<StateBadge value={report.grade} />}
    >
      <Row label="Active memories" value={String(report.total_active)} />
      <Row label="Findings" value={String(report.findings.length)} />

      {report.findings.length === 0 ? (
        <p style={{
          fontFamily: "var(--mono)", fontSize: "0.7rem", color: "var(--muted)",
          margin: "1rem 0 0", lineHeight: 1.7,
        }}>
          Nothing needs attention.
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: "1rem 0 0", padding: 0 }}>
          {report.findings.map((finding) => (
            <li
              key={`${finding.memory_id}-${finding.issue}`}
              style={{
                borderTop: "1px solid var(--line)", padding: "0.85rem 0",
                display: "flex", gap: "1rem", alignItems: "flex-start",
                justifyContent: "space-between", flexWrap: "wrap",
              }}
            >
              <div style={{ minWidth: 0, flex: "1 1 16rem" }}>
                <StateBadge value={finding.issue.replace(/_/g, " ")} />
                <p style={{
                  fontSize: "0.78rem", color: "var(--silver)",
                  margin: "0.5rem 0 0.25rem", lineHeight: 1.5,
                }}>
                  {finding.evidence}
                </p>
                <p style={{
                  fontFamily: "var(--mono)", fontSize: "0.62rem",
                  color: "var(--muted)", margin: 0, letterSpacing: "0.05em",
                }}>
                  {REMEDY_NOTE[finding.remedy] ?? finding.remedy}
                </p>
              </div>
              <button
                type="button"
                onClick={() => { void apply(finding); }}
                disabled={busy === finding.memory_id}
                style={{
                  fontFamily: "var(--mono)", fontSize: "0.62rem",
                  letterSpacing: "0.1em", textTransform: "uppercase",
                  color: "var(--warm)", background: "transparent",
                  border: "1px solid var(--line)", borderRadius: 3,
                  padding: "0.4rem 0.7rem",
                  cursor: busy === finding.memory_id ? "wait" : "pointer",
                  opacity: busy === finding.memory_id ? 0.5 : 1,
                }}
              >
                {busy === finding.memory_id ? "Applying…" : finding.remedy}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
