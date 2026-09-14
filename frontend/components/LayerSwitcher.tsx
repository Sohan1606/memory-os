"use client";
/**
 * Memory layers. Each layer is a real projection over live memory state -
 * counts and contents come from the store, never from static copy.
 */
import { useMemo, useState } from "react";

import MemoryGraph from "@/components/MemoryGraph";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import type { Memory } from "@/lib/types";

type LayerKey = "SHORT-TERM" | "LONG-TERM" | "PREFERENCES" | "PROJECTS" | "CONTEXT";

const DESCRIPTIONS: Record<LayerKey, string> = {
  "SHORT-TERM": "Memories captured in the last 14 days, still settling into the system.",
  "LONG-TERM": "Durable knowledge that has persisted well beyond the current conversation.",
  PREFERENCES: "How this person wants the assistant to behave.",
  PROJECTS: "What they are actively building.",
  CONTEXT: "Surrounding facts, habits and relationships.",
};

function select(layer: LayerKey, memories: Memory[]): Memory[] {
  const now = Date.now();
  const days = (m: Memory) =>
    (now - new Date(m.created_at).getTime()) / 86_400_000;
  switch (layer) {
    case "SHORT-TERM": return memories.filter((m) => days(m) <= 14);
    case "LONG-TERM": return memories.filter((m) => days(m) > 14);
    case "PREFERENCES":
      return memories.filter((m) => m.category === "PREFERENCE" || m.category === "COMMUNICATION_STYLE");
    case "PROJECTS": return memories.filter((m) => m.category === "PROJECT" || m.category === "GOAL");
    case "CONTEXT":
      return memories.filter((m) => ["CONTEXT", "HABIT", "RELATIONSHIP", "FACT", "IDENTITY"].includes(m.category));
  }
}

const LAYERS: LayerKey[] = ["SHORT-TERM", "LONG-TERM", "PREFERENCES", "PROJECTS", "CONTEXT"];

export default function LayerSwitcher() {
  const { memories, graph, select: selectMemory } = useMemoryStore();
  const [layer, setLayer] = useState<LayerKey>("LONG-TERM");

  const visible = useMemo(() => select(layer, memories), [layer, memories]);

  // Re-derive relationships within the filtered subset so the layer graph shows
  // real edges rather than disconnected dots.
  const layerGraph = useMemo(() => {
    const ids = new Set(visible.map((m) => m.id));
    return {
      nodes: visible,
      edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    };
  }, [visible, graph.edges]);

  return (
    <div style={{ display: "grid", gap: "1.6rem" }}>
      <div role="tablist" aria-label="Memory layers"
           style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {LAYERS.map((l) => (
          <button key={l} role="tab" aria-selected={layer === l} className="chip"
                  data-active={layer === l} onClick={() => setLayer(l)}>
            {l} · {select(l, memories).length}
          </button>
        ))}
      </div>

      <p className="body" style={{ maxWidth: "58ch" }}>{DESCRIPTIONS[layer]}</p>

      <div className="layer-split" style={{ display: "grid", gap: "1.5rem" }}>
        <div className="panel" style={{ padding: "0.5rem" }}>
          <MemoryGraph data={layerGraph} height={380} compact />
        </div>
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid",
                     gap: "0.5rem", maxHeight: 380, overflowY: "auto" }}>
          {visible.length === 0 && <li className="body">No memories in this layer yet.</li>}
          {visible.map((m) => (
            <li key={m.id}>
              <button className="panel" onClick={() => selectMemory(m.id)}
                      style={{ width: "100%", textAlign: "left", padding: "0.75rem 0.9rem",
                               background: "transparent", cursor: "pointer" }}>
                <span className="label label-accent">{m.category.replace(/_/g, " ")}</span>
                <span className="body" style={{ display: "block", fontSize: "0.875rem",
                                                marginTop: "0.25rem", color: "var(--warm)" }}>
                  {m.content}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <style>{`
        @media (min-width: 980px) { .layer-split { grid-template-columns: 1fr 1fr; } }
      `}</style>
    </div>
  );
}
