"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CapabilityTrustEntry, CognitivePolicy } from "@/lib/types";
import { Empty, Meter, Panel, StateBadge } from "./primitives";

/**
 * V8.2 §15 / §17 — adaptive behaviour and per-capability trust.
 *
 * Two honesty rules are visible here:
 *   * A learned policy always shows the evidence count behind it, and can be
 *     reverted; defaults are labelled DEFAULT, not presented as learned.
 *   * Trust below the evidence threshold reads INSUFFICIENT EVIDENCE with no
 *     number attached, rather than an optimistic score.
 */
export default function CognitivePolicyPanel({ refreshKey = 0 }:
  { refreshKey?: number }) {
  const [policies, setPolicies] = useState<CognitivePolicy[] | null>(null);
  const [trust, setTrust] = useState<CapabilityTrustEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        api.cognitivePolicy(), api.capabilityTrust(),
      ]);
      setPolicies(p.policies);
      setTrust(t.capabilities);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const revert = async (key: string) => {
    try {
      await api.revertPolicy(key);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revert");
    }
  };

  if (error) return <Panel title="Adaptive behaviour"><Empty>{error}</Empty></Panel>;
  if (!policies || !trust) {
    return <Panel title="Adaptive behaviour"><Empty>Loading…</Empty></Panel>;
  }

  const learned = policies.filter((p) => !p.is_default);

  return (
    <Panel
      title="Adaptive behaviour"
      hint="How the system has adapted to you, and how reliable it has actually
            proven to be. Anything it has not measured says so."
      right={<StateBadge value={`${learned.length} LEARNED`} />}
    >
      {learned.length === 0 ? (
        <Empty>
          Nothing learned yet — all behaviour is at its default. Tell the
          assistant how you want it to respond and it will adapt.
        </Empty>
      ) : learned.map((policy) => (
        <div key={policy.key} style={{ padding: "0.6rem 0",
                                       borderBottom: "1px solid var(--line)" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", gap: "1rem" }}>
            <span style={{ fontSize: "0.8rem", color: "var(--silver)" }}>
              {policy.key.replace(/_/g, " ")}
            </span>
            <span style={{ display: "inline-flex", alignItems: "center",
                           gap: "0.55rem" }}>
              <StateBadge value={policy.value} />
              <button type="button" onClick={() => void revert(policy.key)}
                      style={buttonStyle} title="Undo this learned behaviour">
                Revert
              </button>
            </span>
          </div>
          <div style={{ marginTop: "0.4rem" }}>
            <Meter value={policy.confidence}
                   label={`${Math.round(policy.confidence * 100)}% · ` +
                          `${policy.evidence_count} observation(s)`} />
          </div>
        </div>
      ))}

      <h4 style={{ fontFamily: "var(--mono)", fontSize: "0.62rem",
                   letterSpacing: "0.12em", textTransform: "uppercase",
                   color: "var(--silver)", margin: "1.4rem 0 0.6rem" }}>
        Demonstrated reliability
      </h4>
      {trust.map((entry) => (
        <div key={`${entry.capability}:${entry.task_class}`}
             style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "center", gap: "1rem", padding: "0.45rem 0",
                      borderBottom: "1px solid var(--line)" }}>
          <span style={{ fontSize: "0.78rem", color: "var(--silver)" }}>
            {entry.capability.replace(/_/g, " ")}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center",
                         gap: "0.6rem" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: "0.66rem",
                           color: "var(--muted)" }}>
              {entry.successes}/{entry.total}
            </span>
            <StateBadge value={entry.label} title={entry.detail} />
          </span>
        </div>
      ))}
    </Panel>
  );
}

const buttonStyle = {
  fontFamily: "var(--mono)", fontSize: "0.6rem", letterSpacing: "0.08em",
  textTransform: "uppercase" as const, color: "var(--muted)",
  background: "transparent", border: "1px solid var(--line)",
  borderRadius: 3, padding: "0.22rem 0.5rem", cursor: "pointer",
};
