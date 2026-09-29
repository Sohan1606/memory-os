"use client";
/**
 * ZORQ navigation (§12.3 deliverable 1 — information architecture).
 * Grouped by user intent, not backend modules. The header carries a live
 * status readout rendered ONLY from authoritative backend state
 * (connection LOCAL today, provider posture, memory authority).
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { MetaBadge } from "@/components/zorq/Primitives";
import StateToken from "@/components/zorq/StateToken";
import { api, zorqApi } from "@/lib/api";
import { providerPosture, type ProviderPosture } from "@/lib/provider";
import type { Health, ZorqStatusResponse } from "@/lib/types";

const GROUPS: { group: string; links: { href: string; label: string }[] }[] = [
  {
    group: "Work",
    links: [
      { href: "/", label: "Home" },
      { href: "/workspace", label: "Workspace" },
      { href: "/memory", label: "Memory" },
      { href: "/observatory", label: "Observatory" },
    ],
  },
  {
    group: "Control",
    links: [
      { href: "/actions", label: "Actions" },
      { href: "/capabilities", label: "Capabilities" },
      { href: "/audit", label: "Audit" },
      { href: "/system", label: "System" },
    ],
  },
  {
    group: "Runtime",
    links: [
      { href: "/devices", label: "Devices" },
    ],
  },
];

const ALL_LINKS = GROUPS.flatMap((g) => g.links);

interface HeaderStatus {
  connection: string;
  posture: ProviderPosture;
  memoryAuthority: string | null;
}

function StatusStrip({ status }: { status: HeaderStatus | null }) {
  if (!status) {
    return (
      <span style={{ display: "inline-flex", gap: "0.45rem", flexWrap: "wrap", justifyContent: "flex-end" }}>
        <MetaBadge muted note="Status not yet loaded">SYSTEM · · ·</MetaBadge>
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: "0.45rem", flexWrap: "wrap", justifyContent: "flex-end", alignItems: "center" }}>
      {/* LOCAL is a connection state, not an operation — steady, never pulsing */}
      <StateToken
        value={status.connection}
        note="ZORQ is served by the local runtime. This is today's truthful connection state."
      />
      <MetaBadge
        muted={status.posture.state !== "REAL"}
        note={status.posture.note}
      >
        {status.posture.label}
      </MetaBadge>
      {status.memoryAuthority && (
        <MetaBadge note="MEMORY//OS is the canonical memory and governance subsystem.">
          MEMORY//OS
        </MetaBadge>
      )}
    </span>
  );
}

export default function Navigation() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<HeaderStatus | null>(null);
  const pathname = usePathname();

  useEffect(() => { setOpen(false); }, [pathname]);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  const load = useCallback(async () => {
    // providerPosture() is the single authoritative mapping: REAL AGENT only
    // for mode === "REAL AGENT"; DETERMINISTIC FALLBACK renders as such; an
    // unreachable provider renders UNKNOWN — never defaulted to REAL or DEMO.
    const [health, zorq] = await Promise.all([
      api.health().catch(() => null),
      zorqApi.status().catch(() => null),
    ]) as [Health | null, ZorqStatusResponse | null];
    setStatus({
      connection: zorq && zorq.available ? zorq.runtime.connection : "LOCAL",
      posture: providerPosture(health),
      memoryAuthority: zorq && zorq.available ? zorq.memory_integration.canonical_authority : null,
    });
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <>
      <header style={{
        position: "fixed", top: 0, left: 0, right: 0, zIndex: 900,
        background: "linear-gradient(180deg, rgba(26,15,20,0.92), rgba(13,7,9,0.88))",
        backdropFilter: "blur(10px)", WebkitBackdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--z-line-strong)",
      }}>
        <div style={{
          maxWidth: 1600, margin: "0 auto",
          padding: "0.5rem var(--pad)",
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1.5rem",
        }}>
          <Link href="/" aria-label="ZORQ home"
                style={{ textDecoration: "none", display: "inline-flex", alignItems: "baseline", gap: "0.55rem" }}>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "0.92rem", fontWeight: 600,
              letterSpacing: "0.3em", color: "var(--z-ink)",
            }}>ZORQ</span>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "0.5rem", letterSpacing: "0.24em",
              color: "var(--z-accent)", whiteSpace: "nowrap",
            }}>SYSTEM INTELLIGENCE</span>
          </Link>

          <nav aria-label="Primary" className="nav-desktop">
            {GROUPS.map((group) => (
              <div key={group.group} style={{ display: "flex", alignItems: "center", gap: "0.9rem" }}>
                <span className="z-nav-group" style={{ margin: 0 }} aria-hidden="true">{group.group}</span>
                {group.links.map((l) => (
                  <Link key={l.href} href={l.href} className="z-nav-link"
                        aria-current={isActive(l.href) ? "page" : undefined}>
                    {l.label}
                  </Link>
                ))}
              </div>
            ))}
          </nav>

          <div className="nav-desktop">
            <StatusStrip status={status} />
          </div>

          <div className="nav-mobile-status">
            {status && (
              <StateToken value={status.connection} note="ZORQ is served by the local runtime." />
            )}
            <button className="nav-mobile-trigger" onClick={() => setOpen(true)}
                    aria-label="Open menu" aria-expanded={open}
                    style={{
                      background: "none", border: "1px solid var(--z-line-strong)",
                      padding: "0.5rem 0.85rem", cursor: "pointer", color: "var(--z-ink-2)",
                      fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.14em",
                    }}>
              MENU
            </button>
          </div>
        </div>
      </header>

      {open && (
        <div role="dialog" aria-modal="true" aria-label="Navigation menu"
             style={{
               position: "fixed", inset: 0, zIndex: 950, background: "var(--z-field)",
               display: "flex", flexDirection: "column", padding: "1.4rem var(--pad) 2.5rem",
               overflowY: "auto",
             }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem" }}>
            <span style={{
              fontFamily: "var(--mono)", fontSize: "0.95rem", fontWeight: 600,
              letterSpacing: "0.26em", color: "var(--z-ink)",
            }}>ZORQ</span>
            <button onClick={() => setOpen(false)} aria-label="Close menu"
                    style={{
                      background: "none", border: "1px solid var(--z-line-strong)",
                      padding: "0.5rem 0.85rem", cursor: "pointer", color: "var(--z-ink-2)",
                      fontFamily: "var(--mono)", fontSize: "0.62rem", letterSpacing: "0.14em",
                    }}>
              CLOSE
            </button>
          </div>

          <div style={{ marginBottom: "1.6rem" }}>
            <StatusStrip status={status} />
          </div>

          <nav aria-label="Mobile" style={{ display: "flex", flexDirection: "column", gap: "1.9rem" }}>
            {GROUPS.map((group) => (
              <div key={group.group}>
                <p className="z-nav-group" style={{ marginBottom: "0.7rem" }}>{group.group}</p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
                  {group.links.map((l) => (
                    <Link key={l.href} href={l.href} onClick={() => setOpen(false)}
                          aria-current={isActive(l.href) ? "page" : undefined}
                          style={{
                            textDecoration: "none",
                            fontFamily: "var(--mono)", fontSize: "0.95rem", letterSpacing: "0.12em",
                            textTransform: "uppercase",
                            color: isActive(l.href) ? "var(--z-accent)" : "var(--z-ink-2)",
                            padding: "0.85rem 0.2rem",
                            borderBottom: "1px solid var(--z-line)",
                            display: "flex", justifyContent: "space-between", alignItems: "center",
                            minHeight: "3rem",
                          }}>
                      {l.label}
                      <span aria-hidden="true" style={{ opacity: 0.5, fontSize: "0.7rem" }}>→</span>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </div>
      )}
    </>
  );
}

export { ALL_LINKS };
