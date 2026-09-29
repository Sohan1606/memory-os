"use client";
/**
 * Slim ZORQ footer: identity, subsystem credit, live state line.
 * No marketing framing (retired with the showcase composition in WP-UI-2).
 */
import Link from "next/link";

import { useMemoryStore } from "@/hooks/useMemoryStore";

const LINKS = [
  { href: "/workspace", label: "Workspace" },
  { href: "/memory", label: "Memory" },
  { href: "/actions", label: "Actions" },
  { href: "/capabilities", label: "Capabilities" },
  { href: "/audit", label: "Audit" },
  { href: "/devices", label: "Devices" },
  { href: "/system", label: "System" },
  { href: "/observatory", label: "Observatory" },
];

export default function Footer() {
  const { health } = useMemoryStore();
  return (
    <footer style={{ borderTop: "1px solid var(--z-line)", padding: "2.2rem var(--pad) 2rem", marginTop: "2rem" }}>
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "2rem", flexWrap: "wrap", alignItems: "baseline" }}>
          <span style={{
            fontFamily: "var(--mono)", fontSize: "0.78rem", fontWeight: 600,
            letterSpacing: "0.26em", color: "var(--z-ink-2)",
          }}>ZORQ</span>
          <nav aria-label="Footer" style={{ display: "flex", gap: "1.1rem", flexWrap: "wrap" }}>
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} className="z-nav-link">{l.label}</Link>
            ))}
          </nav>
        </div>
        <div style={{
          display: "flex", justifyContent: "space-between", gap: "1.5rem", flexWrap: "wrap",
          marginTop: "1.4rem", paddingTop: "1.1rem", borderTop: "1px solid var(--z-line)",
        }}>
          <p className="mono" style={{ color: "var(--z-ink-3)", margin: 0 }}>
            Memory &amp; governance — <span style={{ color: "var(--accent)" }}>MEMORY//OS</span>
          </p>
          <p className="mono" style={{ color: "var(--z-ink-3)", margin: 0 }}>
            {health
              ? `${health.provider.name.toUpperCase()} · ${health.vector.mode.toUpperCase()} · CKPT ${health.agent.checkpointer.toUpperCase()}`
              : "connecting…"}
          </p>
        </div>
      </div>
    </footer>
  );
}
