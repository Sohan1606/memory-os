"use client";
/**
 * Product gallery: layered, perspective-framed renditions of real product
 * surfaces. Each panel is drawn with CSS/SVG - no raster screenshots, no
 * external images.
 */
import { useState } from "react";

interface Screen { id: string; title: string; caption: string }

const SCREENS: Screen[] = [
  { id: "conversation", title: "Conversation", caption: "Agent replies with memory citations attached." },
  { id: "graph", title: "Memory network", caption: "Real relationships between stored memories." },
  { id: "retrieval", title: "Retrieval", caption: "Ranked matches, each explaining why it matched." },
  { id: "inspector", title: "Inspector", caption: "Confidence, importance, version, provenance." },
  { id: "voice", title: "Voice mode", caption: "Speech enters the same memory pipeline." },
  { id: "timeline", title: "Timeline", caption: "Every mutation recorded as an audit event." },
  { id: "architecture", title: "Architecture", caption: "The stack, reported from live health." },
];

function Artwork({ id }: { id: string }) {
  const common = { width: "100%", height: "100%", display: "block" } as const;
  switch (id) {
    case "conversation":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          <rect x="26" y="30" width="180" height="34" rx="2" fill="rgba(244,241,234,0.07)" />
          <rect x="150" y="80" width="224" height="46" rx="2" fill="rgba(110,231,215,0.12)" stroke="rgba(110,231,215,0.35)" />
          <rect x="26" y="142" width="240" height="58" rx="2" fill="rgba(244,241,234,0.05)" />
          <rect x="26" y="210" width="96" height="14" rx="2" fill="rgba(110,231,215,0.35)" />
          {[0, 1, 2].map((i) => (
            <circle key={i} cx={40 + i * 16} cy={218} r="2.5" fill="#6ee7d7" opacity={0.8 - i * 0.2} />
          ))}
        </svg>
      );
    case "graph":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          {[[120, 90], [220, 70], [290, 140], [180, 160], [90, 180], [250, 200]].map(([x, y], i, arr) => (
            <g key={i}>
              {arr.slice(i + 1).map(([x2, y2], j) => {
                const d = Math.hypot(x - x2, y - y2);
                return d < 130 ? (
                  <line key={j} x1={x} y1={y} x2={x2} y2={y2}
                        stroke="#6ee7d7" strokeWidth="0.7" opacity={0.35} />
                ) : null;
              })}
            </g>
          ))}
          {[[120, 90, 6], [220, 70, 4], [290, 140, 5], [180, 160, 8], [90, 180, 4], [250, 200, 5]].map(([x, y, r], i) => (
            <circle key={i} cx={x} cy={y} r={r} fill={i % 2 ? "#f4f1ea" : "#6ee7d7"} opacity={0.9} />
          ))}
        </svg>
      );
    case "retrieval":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          <rect x="26" y="26" width="348" height="30" rx="2" fill="rgba(244,241,234,0.06)" />
          {[0, 1, 2].map((i) => (
            <g key={i}>
              <rect x="26" y={78 + i * 56} width="348" height="42" rx="2"
                    fill="rgba(255,255,255,0.03)" stroke="rgba(244,241,234,0.08)" />
              <rect x="38" y={90 + i * 56} width={200 - i * 40} height="7" rx="1" fill="rgba(244,241,234,0.4)" />
              <rect x="38" y={104 + i * 56} width={120 - i * 22} height="5" rx="1" fill="rgba(244,241,234,0.18)" />
              <rect x={320} y={90 + i * 56} width={42 - i * 6} height="7" rx="1" fill="#6ee7d7" opacity={0.85 - i * 0.2} />
            </g>
          ))}
        </svg>
      );
    case "inspector":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          <rect x="210" y="0" width="190" height="260" fill="rgba(255,255,255,0.028)" stroke="rgba(244,241,234,0.12)" />
          <rect x="226" y="28" width="120" height="9" rx="1" fill="#6ee7d7" opacity="0.8" />
          <rect x="226" y="52" width="150" height="7" rx="1" fill="rgba(244,241,234,0.5)" />
          {[0, 1, 2, 3, 4].map((i) => (
            <g key={i}>
              <rect x="226" y={90 + i * 30} width="48" height="6" rx="1" fill="rgba(244,241,234,0.22)" />
              <rect x="300" y={90 + i * 30} width="62" height="6" rx="1" fill="rgba(110,231,215,0.55)" />
            </g>
          ))}
          <circle cx="105" cy="130" r="30" fill="none" stroke="#6ee7d7" strokeWidth="1" opacity="0.6" />
          <circle cx="105" cy="130" r="7" fill="#6ee7d7" />
        </svg>
      );
    case "voice":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          {Array.from({ length: 42 }, (_, i) => {
            const h = 12 + Math.abs(Math.sin(i * 0.55)) * 96;
            return <rect key={i} x={30 + i * 8.2} y={130 - h / 2} width="3.4" height={h} rx="1.5"
                         fill="#6ee7d7" opacity={0.28 + Math.abs(Math.sin(i * 0.55)) * 0.62} />;
          })}
          <rect x="150" y="212" width="100" height="12" rx="6" fill="rgba(244,241,234,0.12)" />
        </svg>
      );
    case "timeline":
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          <line x1="20" y1="150" x2="380" y2="110" stroke="rgba(244,241,234,0.14)" />
          {[0, 1, 2, 3, 4, 5].map((i) => {
            const x = 44 + i * 62;
            const y = 146 - i * 6.6;
            return (
              <g key={i}>
                <circle cx={x} cy={y} r="4" fill="#6ee7d7" />
                <rect x={x - 26} y={y - 52} width="54" height="32" rx="2"
                      fill="rgba(255,255,255,0.035)" stroke="rgba(244,241,234,0.1)" />
              </g>
            );
          })}
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 400 260" style={common} aria-hidden="true">
          <rect width="400" height="260" fill="#0a0a0d" />
          {[0, 1, 2, 3, 4, 5, 6].map((i) => (
            <g key={i} opacity={0.35 + i * 0.09}>
              <rect x={70 + i * 6} y={26 + i * 28} width="260" height="20" rx="2"
                    fill="rgba(110,231,215,0.08)" stroke="rgba(110,231,215,0.3)" />
            </g>
          ))}
        </svg>
      );
  }
}

export default function Gallery() {
  const [active, setActive] = useState(0);

  return (
    <div>
      <div className="gallery-strip" style={{
        display: "flex", gap: "1.2rem", overflowX: "auto", padding: "1rem 0 1.5rem",
        scrollSnapType: "x mandatory",
      }}>
        {SCREENS.map((s, i) => {
          const isActive = i === active;
          return (
            <figure key={s.id}
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              tabIndex={0}
              data-cursor="view"
              style={{
                flex: "0 0 auto", width: isActive ? "clamp(280px, 46vw, 560px)" : "clamp(200px, 26vw, 320px)",
                margin: 0, scrollSnapAlign: "center", cursor: "pointer", outline: "none",
                transition: "width .7s var(--ease), transform .7s var(--ease), opacity .7s var(--ease)",
                transform: isActive ? "translateY(-10px)" : "translateY(14px)",
                opacity: isActive ? 1 : 0.5,
              }}
            >
              <div className="panel" style={{
                overflow: "hidden",
                borderColor: isActive ? "var(--accent-line)" : "var(--line)",
                boxShadow: isActive ? "0 30px 80px rgba(0,0,0,0.6)" : "none",
                transition: "all .7s var(--ease)",
                aspectRatio: "400 / 260",
              }}>
                <Artwork id={s.id} />
              </div>
              <figcaption style={{ marginTop: "0.85rem" }}>
                <p className="label label-accent">{String(i + 1).padStart(2, "0")} — {s.title}</p>
                <p className="body" style={{ fontSize: "0.8125rem", marginTop: "0.3rem" }}>{s.caption}</p>
              </figcaption>
            </figure>
          );
        })}
      </div>
      <p className="label">Scroll or swipe · {SCREENS.length} product surfaces</p>
    </div>
  );
}
