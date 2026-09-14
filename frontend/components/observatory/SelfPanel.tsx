"use client";
/**
 * Honest capability report.
 *
 * This panel exists specifically so the product never overstates itself: it
 * renders NOT CONFIGURED states with the same weight as ACTIVE ones.
 */
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { SelfReport } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

const NAMES: Record<string, string> = {
  conversation: "Conversation",
  model_tool_calling: "Model tool calling",
  semantic_memory: "Semantic memory",
  persistent_memory: "Persistent memory",
  voice_server: "Server transcription",
  langmem: "LangMem",
  external_context: "Calendar / email / files",
  external_actions: "Actions outside memory",
};

export default function SelfPanel() {
  const [report, setReport] = useState<SelfReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.selfReport()
      .then((r) => { if (!cancelled) setReport(r); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Unavailable."); });
    return () => { cancelled = true; };
  }, []);

  if (error) return <Panel title="Capabilities"><Empty>{error}</Empty></Panel>;
  if (!report) return <Panel title="Capabilities"><Empty>Checking…</Empty></Panel>;

  return (
    <Panel title="Capabilities" hint="What this system can and cannot do right now.">
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {Object.entries(report.capabilities).map(([key, cap]) => (
          <li key={key} style={{
            display: "flex", justifyContent: "space-between", gap: "1rem",
            alignItems: "center", padding: "0.55rem 0",
            borderBottom: "1px solid var(--line)",
          }}>
            <span>
              <span style={{ display: "block", fontSize: "0.82rem", color: "var(--warm)" }}>
                {NAMES[key] ?? key}
              </span>
              <span style={{
                display: "block", fontSize: "0.72rem", color: "var(--muted)",
                marginTop: "0.15rem", lineHeight: 1.5, maxWidth: "46ch",
              }}>{cap.detail}</span>
            </span>
            <StateBadge value={cap.state} />
          </li>
        ))}
      </ul>
      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--silver)",
        margin: "1.2rem 0 0.5rem",
      }}>Known limitations</h4>
      <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
        {report.limitations.map((l) => (
          <li key={l} style={{
            fontSize: "0.78rem", color: "var(--silver)", lineHeight: 1.6,
            marginBottom: "0.3rem",
          }}>{l}</li>
        ))}
      </ul>
    </Panel>
  );
}
