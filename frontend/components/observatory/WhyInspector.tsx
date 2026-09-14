"use client";
/** "Why do you think that?" - object history straight from the event log. */
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { CognitiveEvent } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

interface Props {
  subject: { kind: string; id: string } | null;
  onClose: () => void;
}

export default function WhyInspector({ subject, onClose }: Props) {
  const [events, setEvents] = useState<CognitiveEvent[]>([]);
  const [explanation, setExplanation] = useState("");
  const [reputation, setReputation] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!subject) return;
    let cancelled = false;
    api.why(subject.kind, subject.id)
      .then((d) => {
        if (cancelled) return;
        setEvents(d.events); setExplanation(d.explanation);
        setReputation(d.reputation); setError(null);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Unavailable."); });
    return () => { cancelled = true; };
  }, [subject]);

  useEffect(() => {
    if (!subject) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [subject, onClose]);

  if (!subject) return null;

  const rep = reputation as { reputation?: string; lifecycle?: string } | null;

  return (
    <Panel
      title={`Why · ${subject.kind}`}
      right={
        <button type="button" onClick={onClose} aria-label="Close explanation" style={{
          background: "none", border: "1px solid var(--line-strong)", borderRadius: 3,
          color: "var(--silver)", fontFamily: "var(--mono)", fontSize: "0.6rem",
          padding: "0.25rem 0.5rem", cursor: "pointer",
        }}>Close</button>
      }
    >
      {error && <Empty>{error}</Empty>}
      <p style={{ fontSize: "0.82rem", color: "var(--warm)", lineHeight: 1.6,
                  margin: "0 0 0.8rem" }}>{explanation}</p>

      {rep?.reputation && (
        <div style={{ display: "flex", gap: "0.4rem", marginBottom: "0.9rem" }}>
          <StateBadge value={rep.reputation} />
          {rep.lifecycle && <StateBadge value={rep.lifecycle} />}
        </div>
      )}

      {events.length === 0 ? (
        <Empty>No recorded history for this object.</Empty>
      ) : (
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {events.map((e) => (
            <li key={e.id} style={{
              padding: "0.5rem 0 0.5rem 0.9rem", borderLeft: "1px solid var(--line-strong)",
              marginLeft: "0.2rem",
            }}>
              <span style={{ display: "block", fontSize: "0.8rem", color: "var(--warm)" }}>
                {e.label}
              </span>
              <span style={{
                display: "block", fontFamily: "var(--mono)", fontSize: "0.62rem",
                color: "var(--muted)", marginTop: "0.15rem",
              }}>{new Date(e.created_at).toLocaleString()} · {e.type}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
