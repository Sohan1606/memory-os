"use client";
/**
 * Data-driven editorial timeline built from real memory_events plus the
 * createdAt/updatedAt of live memories. No hard-coded months.
 */
import { useMemo, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";

const LABELS: Record<string, string> = {
  MEMORY_CREATED: "Created",
  MEMORY_UPDATED: "Updated",
  MEMORY_REINFORCED: "Reinforced",
  MEMORY_SUPERSEDED: "Superseded",
  MEMORY_CONSOLIDATED: "Consolidated",
  MEMORY_DELETED: "Deleted",
  MEMORY_RETRIEVED: "Recalled",
};

export default function MemoryTimeline() {
  const { events, memories, select } = useMemoryStore();
  const scroller = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef({ startX: 0, startScroll: 0 });

  const points = useMemo(() => {
    const byId = new Map(memories.map((m) => [m.id, m]));
    const seen = new Set<string>();
    return events
      .filter((e) => e.event_type !== "MEMORY_RETRIEVED")
      .filter((e) => {
        // one marker per memory+type keeps the composition readable
        const key = `${e.memory_id}-${e.event_type}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((e) => ({
        id: e.id,
        memoryId: e.memory_id,
        type: e.event_type,
        at: new Date(e.created_at),
        memory: e.memory_id ? byId.get(e.memory_id) ?? null : null,
      }))
      .filter((p) => !Number.isNaN(p.at.getTime()))
      .sort((a, b) => a.at.getTime() - b.at.getTime());
  }, [events, memories]);

  if (points.length === 0) {
    return <p className="body">No memory events recorded yet.</p>;
  }

  const onDown = (e: React.PointerEvent) => {
    if (!scroller.current) return;
    setDragging(true);
    drag.current = { startX: e.clientX, startScroll: scroller.current.scrollLeft };
    scroller.current.setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging || !scroller.current) return;
    scroller.current.scrollLeft = drag.current.startScroll - (e.clientX - drag.current.startX);
  };
  const onUp = (e: React.PointerEvent) => {
    setDragging(false);
    scroller.current?.releasePointerCapture(e.pointerId);
  };

  return (
    <div>
      <div
        ref={scroller}
        data-cursor="drag"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        role="group"
        aria-label="Memory event timeline, scrollable horizontally"
        style={{
          display: "flex", gap: "1.1rem", overflowX: "auto", paddingBottom: "1.5rem",
          scrollSnapType: "x proximity", cursor: dragging ? "grabbing" : "grab",
        }}
      >
        {points.map((p, i) => {
          const prev = points[i - 1];
          const newMonth = !prev ||
            prev.at.getMonth() !== p.at.getMonth() || prev.at.getFullYear() !== p.at.getFullYear();
          const offset = i % 2 === 0 ? 0 : 54; // diagonal editorial rhythm
          return (
            <div key={p.id} style={{
              flex: "0 0 auto", width: "clamp(210px, 24vw, 280px)",
              marginTop: offset, scrollSnapAlign: "center",
            }}>
              {newMonth && (
                <p className="label label-accent" style={{ marginBottom: "0.5rem" }}>
                  {p.at.toLocaleDateString(undefined, { month: "short", year: "numeric" })}
                </p>
              )}
              <button
                onClick={() => p.memoryId && select(p.memoryId)}
                disabled={!p.memory}
                className="panel"
                style={{
                  width: "100%", textAlign: "left", padding: "1rem",
                  background: "transparent", cursor: p.memory ? "pointer" : "default",
                  borderTop: "1px solid var(--accent-line)",
                }}
              >
                <span className="mono" style={{ color: "var(--accent)" }}>
                  {LABELS[p.type] ?? p.type}
                </span>
                <span className="body" style={{
                  display: "block", fontSize: "0.875rem", marginTop: "0.45rem",
                  color: p.memory ? "var(--warm)" : "var(--muted)",
                }}>
                  {p.memory ? p.memory.content : "This memory no longer exists."}
                </span>
                <span className="mono" style={{
                  display: "block", color: "var(--muted)", marginTop: "0.5rem",
                }}>
                  {p.at.toLocaleDateString(undefined, { day: "2-digit", month: "short" })}
                  {p.memory ? ` · v${p.memory.version}` : ""}
                </span>
              </button>
            </div>
          );
        })}
      </div>
      <p className="label">Drag or scroll horizontally · {points.length} events</p>
    </div>
  );
}
