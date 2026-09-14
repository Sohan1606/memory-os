"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProviderStatusResponse } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

/**
 * Shows exactly what is answering: a real local model or the deterministic
 * fallback. This panel renders the backend's own capability strings verbatim so
 * the UI can never overstate the runtime.
 */
export default function ProviderPanel() {
  const [data, setData] = useState<ProviderStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const next = await api.providerStatus();
        if (active) { setData(next); setError(null); }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Unavailable");
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (error) {
    return <Panel title="Provider"><Empty>{error}</Empty></Panel>;
  }
  if (!data) {
    return <Panel title="Provider"><Empty>Checking provider…</Empty></Panel>;
  }

  const { provider, extraction, perception } = data;

  return (
    <Panel
      title="Provider"
      hint={provider.detail}
      right={<StateBadge value={provider.mode} />}
    >
      <Row label="Provider" value={provider.name.toUpperCase()} />
      <Row label="Model" value={provider.model ?? "NONE"} />
      <Row
        label="Tool calling"
        value={<StateBadge value={provider.tool_calling ? "ACTIVE" : "UNAVAILABLE"} />}
      />
      <Row label="Extraction" value={<StateBadge value={extraction.state} />} />

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--muted)",
        margin: "1.1rem 0 0.4rem",
      }}>
        Perception
      </h4>
      {Object.entries(perception).map(([name, cap]) => (
        <Row
          key={name}
          label={name}
          value={<StateBadge value={cap.state} title={cap.detail} />}
        />
      ))}
    </Panel>
  );
}
