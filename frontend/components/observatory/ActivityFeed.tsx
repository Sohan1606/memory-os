"use client";
/** Live cognitive activity. Polls incrementally via `since` so events never duplicate. */
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { CognitiveEvent } from "@/lib/types";
import { Empty, Panel, StateBadge } from "./primitives";

interface Props { developer: boolean; onSelect?: (e: CognitiveEvent) => void }

export default function ActivityFeed({ developer, onSelect }: Props) {
  const [events, setEvents] = useState<CognitiveEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  const poll = useCallback(async () => {
    try {
      const data = latest.current
        ? await api.cognitiveEvents({ since: latest.current })
        : await api.cognitiveEvents({ limit: 40 });
      setError(null);
      if (data.events.length === 0) return;
      latest.current = Math.max(latest.current, data.latest_id);
      setEvents((prev) => {
        const merged = latest.current && prev.length
          ? [...data.events.slice().reverse(), ...prev]
          : data.events;
        return merged.slice(0, 120);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Activity feed unavailable.");
    }
  }, []);

  useEffect(() => {
    void poll();
    const id = window.setInterval(() => { void poll(); }, 4000);
    return () => window.clearInterval(id);
  }, [poll]);

  return (
    <Panel
      title="Activity"
      hint="Everything the system has done, as it happened."
      right={<StateBadge value={error ? "DEGRADED" : "LIVE"} />}
    >
      {error && <Empty>{error}</Empty>}
      {!error && events.length === 0 && (
        <Empty>No activity yet. Start a conversation and it will appear here.</Empty>
      )}
      <ul style={{ listStyle: "none", margin: 0, padding: 0,
                   maxHeight: 420, overflowY: "auto" }}>
        {events.map((e) => (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onSelect?.(e)}
              style={{
                display: "block", width: "100%", textAlign: "left",
                background: "none", border: "none",
                borderBottom: "1px solid var(--line)", padding: "0.6rem 0",
                cursor: onSelect ? "pointer" : "default", color: "inherit",
              }}
            >
              <span style={{
                display: "flex", justifyContent: "space-between",
                gap: "1rem", alignItems: "baseline",
              }}>
                <span style={{ fontSize: "0.82rem", color: "var(--warm)" }}>
                  {e.label}
                </span>
                <span style={{
                  fontFamily: "var(--mono)", fontSize: "0.62rem",
                  color: "var(--muted)", whiteSpace: "nowrap",
                }}>
                  {new Date(e.created_at).toLocaleTimeString()}
                </span>
              </span>
              {e.summary && (
                <span style={{
                  display: "block", fontSize: "0.75rem", color: "var(--silver)",
                  marginTop: "0.2rem", overflow: "hidden",
                  textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{e.summary}</span>
              )}
              {developer && (
                <span style={{
                  display: "block", fontFamily: "var(--mono)", fontSize: "0.6rem",
                  color: "var(--muted)", marginTop: "0.25rem",
                }}>
                  {e.type}
                  {e.subject_kind ? ` · ${e.subject_kind}:${e.subject_id}` : ""}
                  {e.correlation_id ? ` · ${e.correlation_id}` : ""}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
