"use client";
/**
 * Connected memory graph rendered as SVG from real backend data.
 *
 * Layout is a deterministic category-clustered radial arrangement with a light
 * collision relaxation pass, so the same dataset always produces the same
 * readable picture. Edges come from real relatedMemoryIds; the component never
 * draws an edge to a node that is not present.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";
import type { GraphData, Memory } from "@/lib/types";

const CATEGORY_ORDER = [
  "IDENTITY", "COMMUNICATION_STYLE", "PREFERENCE", "PROJECT",
  "GOAL", "HABIT", "RELATIONSHIP", "FACT", "CONTEXT",
];

const W = 1000;
const H = 700;

export interface GraphHighlight { ids: string[]; pathEdges?: [string, string][] }

interface Props {
  data?: GraphData;
  highlight?: GraphHighlight | null;
  height?: number;
  compact?: boolean;
}

interface Placed { id: string; x: number; y: number; memory: Memory }

function layout(nodes: Memory[]): Placed[] {
  const groups = new Map<string, Memory[]>();
  for (const m of nodes) {
    const key = CATEGORY_ORDER.includes(m.category) ? m.category : "CONTEXT";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(m);
  }
  const cats = CATEGORY_ORDER.filter((c) => groups.has(c));
  const placed: Placed[] = [];
  const cx = W / 2;
  const cy = H / 2;

  cats.forEach((cat, ci) => {
    const list = groups.get(cat)!;
    const clusterAngle = (ci / cats.length) * Math.PI * 2 - Math.PI / 2;
    const clusterR = cats.length === 1 ? 0 : 205;
    const gx = cx + Math.cos(clusterAngle) * clusterR;
    const gy = cy + Math.sin(clusterAngle) * clusterR * 0.78;
    list.forEach((m, i) => {
      const a = (i / Math.max(1, list.length)) * Math.PI * 2 + ci;
      const r = list.length === 1 ? 0 : 34 + Math.min(58, list.length * 8);
      placed.push({ id: m.id, x: gx + Math.cos(a) * r, y: gy + Math.sin(a) * r * 0.85, memory: m });
    });
  });

  // simple relaxation so labels/nodes do not collide
  for (let iter = 0; iter < 60; iter++) {
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i];
        const b = placed[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const min = 46;
        if (d < min) {
          const push = (min - d) / d * 0.5;
          a.x -= dx * push; a.y -= dy * push;
          b.x += dx * push; b.y += dy * push;
        }
      }
    }
  }
  for (const p of placed) {
    p.x = Math.max(48, Math.min(W - 48, p.x));
    p.y = Math.max(44, Math.min(H - 44, p.y));
  }
  return placed;
}

export default function MemoryGraph({ data, highlight, height = 560, compact = false }: Props) {
  const store = useMemoryStore();
  const graph = data ?? store.graph;
  const { selectedId, select } = store;
  const [hovered, setHovered] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [mounted, setMounted] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => { setMounted(true); }, []);

  const placed = useMemo(() => layout(graph.nodes), [graph.nodes]);
  const positions = useMemo(() => {
    const map = new Map<string, Placed>();
    placed.forEach((p) => map.set(p.id, p));
    return map;
  }, [placed]);

  const active = hovered ?? selectedId;
  const relatedIds = useMemo(() => {
    if (!active) return new Set<string>();
    const node = graph.nodes.find((n) => n.id === active);
    return new Set(node?.related_memory_ids ?? []);
  }, [active, graph.nodes]);

  const highlightSet = useMemo(
    () => new Set(highlight?.ids ?? []), [highlight]);

  const validEdges = useMemo(
    () => graph.edges.filter((e) => positions.has(e.source) && positions.has(e.target)),
    [graph.edges, positions]);

  if (graph.nodes.length === 0) {
    return (
      <div className="panel" style={{ height, display: "grid", placeItems: "center" }}>
        <p className="body">No memories yet. Create one and the graph will build itself.</p>
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        role="group"
        aria-label={`Memory graph with ${graph.nodes.length} memories and ${validEdges.length} relationships`}
        style={{
          width: "100%", height, display: "block",
          transform: `scale(${zoom})`, transformOrigin: "center",
          transition: "transform .5s var(--ease)",
        }}
      >
        <g>
          {validEdges.map((e, i) => {
            const a = positions.get(e.source)!;
            const b = positions.get(e.target)!;
            const touchesActive = active === e.source || active === e.target;
            const inPath = highlightSet.has(e.source) && highlightSet.has(e.target);
            const dim = (active && !touchesActive) || (highlightSet.size > 0 && !inPath);
            return (
              <line
                key={`${e.source}-${e.target}-${i}`}
                x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke={inPath || touchesActive ? "var(--accent)" : "var(--warm)"}
                strokeWidth={inPath || touchesActive ? 1.15 : 0.5}
                opacity={dim ? 0.05 : inPath || touchesActive ? 0.75 : 0.16}
                style={{ transition: "opacity .45s var(--ease), stroke-width .45s var(--ease)" }}
              />
            );
          })}
        </g>

        <g>
          {placed.map((p) => {
            const isActive = active === p.id;
            const isRelated = relatedIds.has(p.id);
            const isHighlighted = highlightSet.has(p.id);
            const dim = (active && !isActive && !isRelated) ||
                        (highlightSet.size > 0 && !isHighlighted);
            const r = (isActive ? 9 : isHighlighted ? 7.5 : isRelated ? 6 : 4.6) *
                      (0.85 + p.memory.importance * 0.4);
            const fill = isActive || isHighlighted ? "var(--accent)"
              : isRelated ? "rgba(110,231,215,0.75)" : "var(--warm)";
            return (
              <g
                key={p.id}
                transform={`translate(${p.x},${p.y})`}
                tabIndex={0}
                role="button"
                aria-label={`${p.memory.category}: ${p.memory.content}`}
                aria-pressed={selectedId === p.id}
                data-cursor="node"
                onClick={() => select(selectedId === p.id ? null : p.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    select(selectedId === p.id ? null : p.id);
                  }
                }}
                onMouseEnter={() => setHovered(p.id)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(p.id)}
                onBlur={() => setHovered(null)}
                style={{ cursor: "pointer", outline: "none",
                         opacity: dim ? 0.22 : 1, transition: "opacity .45s var(--ease)" }}
              >
                {(isActive || isHighlighted) && (
                  <circle r={r * 2.9} fill="var(--accent)" opacity={0.1}>
                    {mounted && (
                      <animate attributeName="opacity" values="0.05;0.16;0.05"
                               dur="2.6s" repeatCount="indefinite" />
                    )}
                  </circle>
                )}
                <circle r={r} fill={fill}
                        style={{ transition: "r .4s var(--ease), fill .4s var(--ease)" }} />
                <circle r={r + 9} fill="transparent" />
                {!compact && (isActive || isHighlighted) && (
                  <text y={-r - 11} textAnchor="middle" fill="var(--warm)"
                        style={{ fontFamily: "var(--mono)", fontSize: 11, pointerEvents: "none" }}>
                    {p.memory.content.length > 38
                      ? `${p.memory.content.slice(0, 38)}…`
                      : p.memory.content}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      <div style={{
        position: "absolute", right: 0, bottom: 0, display: "flex", gap: "0.4rem",
      }}>
        <button className="chip" onClick={() => setZoom((z) => Math.max(0.7, +(z - 0.15).toFixed(2)))}
                aria-label="Zoom out">−</button>
        <button className="chip" onClick={() => setZoom(1)} aria-label="Reset zoom">
          {Math.round(zoom * 100)}%
        </button>
        <button className="chip" onClick={() => setZoom((z) => Math.min(1.8, +(z + 0.15).toFixed(2)))}
                aria-label="Zoom in">+</button>
      </div>
    </div>
  );
}
