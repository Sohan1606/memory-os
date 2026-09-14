"use client";
import { useEffect, useState } from "react";
import Link from "next/link";

import MemoryCore from "@/components/MemoryCore";
import { useMemoryStore } from "@/hooks/useMemoryStore";
import { useReducedMotion } from "@/hooks/useReducedMotion";

const WORDS = ["remembers", "understands", "adapts", "persists"];

export default function Hero() {
  const { health, stats } = useMemoryStore();
  const reduced = useReducedMotion();
  const [wordIndex, setWordIndex] = useState(0);

  useEffect(() => {
    if (reduced) return;
    const id = setInterval(() => setWordIndex((i) => (i + 1) % WORDS.length), 2600);
    return () => clearInterval(id);
  }, [reduced]);

  return (
    <header style={{ position: "relative", minHeight: "100svh", display: "flex",
                     alignItems: "center", overflow: "hidden",
                     paddingTop: "clamp(7rem, 14vh, 10rem)",
                     paddingBottom: "clamp(6rem, 12vh, 8rem)" }}>
      <div aria-hidden="true" style={{ position: "absolute", inset: 0, zIndex: 0 }}>
        <MemoryCore height="100%" />
      </div>
      <div style={{
        position: "relative", zIndex: 2, width: "100%", maxWidth: "var(--maxw)",
        margin: "0 auto", padding: "0 var(--pad)",
      }}>
        <p className="label label-accent">MEMORY//OS — Local-first agent memory</p>
        <h1 className="display" style={{ marginTop: "1.4rem", maxWidth: "13ch" }}>
          An AI that actually{" "}
          <span style={{ position: "relative", display: "inline-block", color: "var(--accent)" }}>
            {WORDS[wordIndex]}
          </span>
        </h1>
        <p className="body body-lg" style={{ marginTop: "2rem", maxWidth: "52ch" }}>
          Most assistants forget the moment the tab closes. MEMORY//OS stores what matters,
          notices when it changes, and can explain exactly why it recalled something —
          running entirely on your machine.
        </p>

        <div style={{ display: "flex", gap: "0.7rem", flexWrap: "wrap", marginTop: "2.6rem" }}>
          <Link href="/workspace" className="btn btn-primary" data-cursor="cta">
            Open the workspace
          </Link>
          <Link href="/memory" className="btn">Explore the memory</Link>
        </div>

        <dl style={{ display: "flex", flexWrap: "wrap", gap: "clamp(1.5rem, 3vw, 3rem)", margin: "3.25rem 0 0" }}>
          {[
            ["Memories stored", stats ? String(stats.total) : "—"],
            ["Retrieval", health ? health.vector.mode.toUpperCase() : "—"],
            ["Embeddings", health?.embeddings.dimension ? `${health.embeddings.dimension}-DIM LOCAL` : "—"],
            ["Provider", health ? health.provider.name.toUpperCase() : "—"],
          ].map(([k, v]) => (
            <div key={k}>
              <dt className="label">{k}</dt>
              <dd className="mono" style={{ color: "var(--accent)", margin: "0.4rem 0 0", fontSize: "0.875rem" }}>
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <p className="label" style={{
        position: "absolute", bottom: "1.75rem", right: "var(--pad)", zIndex: 2,
      }}>
        Scroll ↓
      </p>
    </header>
  );
}
