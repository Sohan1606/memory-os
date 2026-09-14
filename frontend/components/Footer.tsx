"use client";
import Link from "next/link";

import { useMemoryStore } from "@/hooks/useMemoryStore";

export default function Footer() {
  const { health } = useMemoryStore();
  return (
    <footer style={{ borderTop: "1px solid var(--line)", padding: "clamp(4rem, 9vh, 7rem) var(--pad) 3rem" }}>
      <div style={{ maxWidth: "var(--maxw)", margin: "0 auto" }}>
        <h2 className="headline" style={{ maxWidth: "16ch" }}>
          Give your agent a memory it can defend.
        </h2>
        <div style={{ display: "flex", gap: "0.7rem", flexWrap: "wrap", marginTop: "2.2rem" }}>
          <Link href="/workspace" className="btn btn-primary" data-cursor="cta">Open the workspace</Link>
          <Link href="/architecture" className="btn">Read the architecture</Link>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", gap: "2rem",
                      flexWrap: "wrap", marginTop: "5rem",
                      borderTop: "1px solid var(--line)", paddingTop: "2rem" }}>
          <p className="mono" style={{ color: "var(--muted)" }}>MEMORY//OS · local-first agent memory</p>
          <p className="mono" style={{ color: "var(--muted)" }}>
            {health
              ? `${health.provider.name.toUpperCase()} · ${health.vector.mode.toUpperCase()} · CKPT ${health.agent.checkpointer.toUpperCase()}`
              : "connecting…"}
          </p>
        </div>
      </div>
    </footer>
  );
}
