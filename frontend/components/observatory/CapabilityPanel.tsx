"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CapabilitiesResponse, RouteDecision } from "@/lib/types";
import { Empty, Panel, Row, StateBadge } from "./primitives";

/**
 * V8.2 §3 — what the active model can genuinely do, and how each task will
 * actually execute right now.
 *
 * Every state string and reason is rendered verbatim from the backend. UNKNOWN
 * is displayed as UNKNOWN: a capability we have not verified is never shown as
 * working.
 */
export default function CapabilityPanel() {
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [routes, setRoutes] = useState<RouteDecision[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [c, r] = await Promise.all([
          api.capabilities(), api.routingTable(),
        ]);
        if (!active) return;
        setCaps(c.capabilities);
        setRoutes(r.routes);
        setError(null);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Unavailable");
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (error) return <Panel title="Model capabilities"><Empty>{error}</Empty></Panel>;
  if (!caps || !routes) {
    return <Panel title="Model capabilities"><Empty>Detecting…</Empty></Panel>;
  }

  const label = (state: string) =>
    state === "NOT_SUPPORTED" ? "NOT AVAILABLE" : state;

  return (
    <Panel
      title="Model capabilities"
      hint="Detected from the active provider and model. UNKNOWN means we have
            not verified it — it is never assumed to work."
      right={<StateBadge value={caps.available ? "ACTIVE" : "NOT CONFIGURED"} />}
    >
      <Row label="Provider" value={caps.provider} />
      <Row label="Model" value={caps.model ?? "NONE"} />

      <div style={{ marginTop: "1rem" }}>
        {caps.capabilities.map((cap) => (
          <div key={cap.name} style={{ padding: "0.55rem 0",
                                       borderBottom: "1px solid var(--line)" }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: "1rem" }}>
              <span style={{ fontSize: "0.8rem", color: "var(--silver)" }}>
                {cap.name.replace(/_/g, " ")}
              </span>
              <StateBadge value={label(cap.state)} title={cap.reason} />
            </div>
            <p style={{ fontSize: "0.72rem", color: "var(--muted)",
                        margin: "0.35rem 0 0", lineHeight: 1.5 }}>
              {cap.reason}
            </p>
          </div>
        ))}
      </div>

      <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                   letterSpacing: "0.12em", textTransform: "uppercase",
                   color: "var(--silver)", margin: "1.4rem 0 0.6rem" }}>
        How each task will run
      </h4>
      {routes.length === 0 ? <Empty>No routing information.</Empty> : routes.map((route) => (
        <div key={route.task} style={{ padding: "0.5rem 0",
                                       borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", gap: "1rem" }}>
            <span style={{ fontSize: "0.78rem", color: "var(--silver)" }}>
              {route.task.replace(/_/g, " ")}
            </span>
            <StateBadge
              value={route.mode === "NOT_CONFIGURED" ? "NOT CONFIGURED"
                   : route.degraded ? "DEGRADED" : route.mode}
              title={route.reason}
            />
          </div>
          <p style={{ fontSize: "0.72rem", color: "var(--muted)",
                      margin: "0.3rem 0 0", lineHeight: 1.5 }}>
            {route.reason}
          </p>
        </div>
      ))}
    </Panel>
  );
}
