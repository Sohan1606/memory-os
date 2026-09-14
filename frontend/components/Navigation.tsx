"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/#product", label: "Product" },
  { href: "/memory", label: "Memory" },
  { href: "/architecture", label: "System" },
  { href: "/workspace", label: "Experience" },
  { href: "/observatory", label: "Observatory" },
];

export default function Navigation() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => { setOpen(false); }, [pathname]);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  return (
    <>
      <header style={{
        position: "fixed", top: 0, left: 0, right: 0, zIndex: 900,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: scrolled ? "0.85rem var(--pad)" : "1.6rem var(--pad)",
        background: scrolled ? "rgba(5,5,6,0.72)" : "transparent",
        backdropFilter: scrolled ? "blur(14px)" : "none",
        WebkitBackdropFilter: scrolled ? "blur(14px)" : "none",
        borderBottom: `1px solid ${scrolled ? "var(--line)" : "transparent"}`,
        transition: "all .55s var(--ease)",
      }}>
        <Link href="/" className="mono" style={{
          color: "var(--warm)", textDecoration: "none", letterSpacing: "0.16em",
          fontSize: "0.8125rem", fontWeight: 500,
        }} data-cursor="cta">
          MEMORY<span style={{ color: "var(--accent)" }}>{"//"}</span>OS
        </Link>

        <nav aria-label="Primary" className="nav-desktop"
             style={{ display: "none", gap: "2.2rem", alignItems: "center" }}>
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="label"
                  style={{ textDecoration: "none", color: "var(--silver)" }}>
              {l.label}
            </Link>
          ))}
          <Link href="/workspace" className="btn btn-primary" data-cursor="cta"
                style={{ padding: "0.6rem 1.15rem" }}>
            Enter
          </Link>
        </nav>

        <button className="nav-mobile-trigger" onClick={() => setOpen(true)}
                aria-label="Open menu" aria-expanded={open}
                style={{
                  background: "none", border: "1px solid var(--line-strong)",
                  padding: "0.55rem 0.9rem", cursor: "pointer",
                }}>
          <span className="label">Menu</span>
        </button>
      </header>

      {open && (
        <div role="dialog" aria-modal="true" aria-label="Navigation menu"
             style={{
               position: "fixed", inset: 0, zIndex: 950, background: "var(--black)",
               display: "flex", flexDirection: "column", padding: "var(--pad)",
             }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="mono" style={{ letterSpacing: "0.16em" }}>
              MEMORY<span style={{ color: "var(--accent)" }}>{"//"}</span>OS
            </span>
            <button onClick={() => setOpen(false)} aria-label="Close menu"
                    style={{ background: "none", border: "1px solid var(--line-strong)",
                             padding: "0.55rem 0.9rem", cursor: "pointer" }}>
              <span className="label">Close</span>
            </button>
          </div>
          <nav aria-label="Mobile" style={{
            flex: 1, display: "flex", flexDirection: "column", justifyContent: "center",
            gap: "0.4rem",
          }}>
            {LINKS.map((l, i) => (
              <Link key={l.href} href={l.href}
                    style={{
                      textDecoration: "none", color: "var(--warm)",
                      fontSize: "clamp(2rem,9vw,3.5rem)", fontWeight: 200,
                      letterSpacing: "-0.04em", padding: "0.35rem 0",
                      borderBottom: "1px solid var(--line)",
                      animation: `menuIn .5s var(--ease) ${i * 0.07}s both`,
                    }}>
                {l.label}
              </Link>
            ))}
            <Link href="/workspace" className="btn btn-primary"
                  style={{ marginTop: "2rem", justifyContent: "center" }}>
              Enter the experience
            </Link>
          </nav>
        </div>
      )}

      <style>{`
        @keyframes menuIn { from { opacity:0; transform: translateY(18px);} to {opacity:1; transform:none;} }
        @media (min-width: 900px) {
          .nav-desktop { display: flex !important; }
          .nav-mobile-trigger { display: none !important; }
        }
      `}</style>
    </>
  );
}
