"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MetricsSnapshot, ReadinessReport } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

/**
 * V8.5 operational health: readiness vs liveness, per-dependency truth
 * (ACTIVE / DEGRADED / NOT_CONFIGURED / BLOCKED / FAILED), error counts and
 * request latency. Every value is the backend's own measurement — nothing is
 * synthesized in the UI, and no cognitive content ever appears here.
 */
export default function SystemHealthPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const ready = await api.readiness();
        if (!active) return;
        setReadiness(ready); setError(null);
        try {
          const snap = await api.metrics();
          if (active) setMetrics(snap);
        } catch {
          if (active) setMetrics(null);   // metrics need health.read
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Unavailable");
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 20000);
    return () => { active = false; window.clearInterval(timer); };
  }, [refreshKey]);

  if (error) return <Panel title="System health"><Empty>{error}</Empty></Panel>;
  if (!readiness) return <Panel title="System health"><Empty>Checking readiness…</Empty></Panel>;

  const errorCount = metrics?.counters["http.errors_5xx"] ?? 0;
  const authFailures = metrics?.counters["security.auth_failures"] ?? 0;
  const rateLimited = metrics?.counters["security.rate_limited"] ?? 0;
  const totalRequests = metrics?.counters["http.requests_total"] ?? 0;

  const slowest = metrics
    ? Object.entries(metrics.requests).sort((a, b) => b[1].avg_ms - a[1].avg_ms).slice(0, 5)
    : [];

  return (
    <Panel
      title="System health"
      hint="Readiness is measured per dependency; a running process alone is not health."
      right={<StateBadge value={readiness.status === "ready" ? "ACTIVE" : "AT_RISK"} />}
    >
      <Row label="Readiness" value={readiness.status.toUpperCase()} />
      {readiness.degraded_capabilities.length > 0 && (
        <Row label="Degraded" value={readiness.degraded_capabilities.join(", ")} />
      )}

      <h4 style={{
        fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
        textTransform: "uppercase", color: "var(--muted)", margin: "1.1rem 0 0.4rem",
      }}>
        Dependencies
      </h4>
      {Object.entries(readiness.dependencies).map(([name, dep]) => (
        <Row
          key={name}
          label={name.replace(/_/g, " ")}
          value={<StateBadge value={dep.state.replace(/_/g, " ")} title={dep.detail} />}
        />
      ))}

      {metrics && (
        <>
          <h4 style={{
            fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.12em",
            textTransform: "uppercase", color: "var(--muted)", margin: "1.1rem 0 0.4rem",
          }}>
            Requests ({metrics.scope})
          </h4>
          <Row label="Total requests" value={String(totalRequests)} />
          <Row label="5xx errors" value={String(errorCount)} />
          <Row label="Auth failures" value={String(authFailures)} />
          <Row label="Rate limited" value={String(rateLimited)} />
          {slowest.map(([route, stats]) => (
            <Row
              key={route}
              label={route}
              value={`${stats.avg_ms}ms avg · ${stats.count} req`}
            />
          ))}
        </>
      )}
      {!metrics && (
        <Empty>Request metrics require an authenticated session with health access.</Empty>
      )}
    </Panel>
  );
}
