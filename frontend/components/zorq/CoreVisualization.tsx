"use client";
/**
 * ZORQ core visualization — the central intelligence instrument.
 *
 * A radial system diagram rendered ONLY from real backend state:
 *  - five outer arc segments = the five architecture planes, each styled by
 *    its truthful status (implemented/partial = drawn; designed = dashed)
 *  - inner ring = runtime states (connection, sync, voice, multimodal)
 *  - core = ZORQ identity + live connection state
 *  - orbit nodes = memory authority, provider posture, audit integrity,
 *    capability count — real values only
 *
 * No invented percentages, no fake cognition motion. The only motion is a
 * slow structural rotation of a fine guide ring (killed under
 * prefers-reduced-motion) and the pulse of genuinely-active states.
 * When state is unavailable the instrument renders neutral and labeled
 * honestly (UNKNOWN / UNAVAILABLE) — it never implies readiness.
 *
 * DETERMINISTIC GEOMETRY: every generated coordinate is rounded to a fixed
 * precision (2 decimals) before it is written into an SVG attribute, so the
 * server render and the client hydration produce byte-identical markup on
 * any JavaScript engine. No Date.now(), no Math.random(), no
 * layout-dependent values are used during render.
 */
import type { Health } from "@/lib/types";
import type { ZorqStatus } from "@/lib/types";
import { providerPosture } from "@/lib/provider";

interface Props {
  zorq: ZorqStatus | null;
  health: Health | null;
  size?: number;
  busy?: boolean;
}

function planeSegmentTone(status: string): { stroke: string; dash?: string; opacity: number } {
  const s = status.toUpperCase();
  if (s === "IMPLEMENTED") return { stroke: "var(--z-ok)", opacity: 0.95 };
  if (s.startsWith("PARTIALLY")) return { stroke: "var(--z-ok)", opacity: 0.55, dash: "4 5" };
  if (s === "DESIGNED") return { stroke: "var(--z-ink-3)", opacity: 0.7, dash: "2 6" };
  return { stroke: "var(--z-unknown)", opacity: 0.5, dash: "2 6" };
}

function runtimeTone(state: string): string {
  const v = state.toUpperCase();
  if (v === "LOCAL" || v === "SYNCED" || v === "READY") return "var(--z-ok)";
  if (v.includes("NOT-") || v === "UNAVAILABLE" || v === "NOT CONFIGURED") return "var(--z-ink-3)";
  if (v === "SYNCING" || v === "ACTIVE") return "var(--z-accent)";
  return "var(--z-unknown)";
}

/** Fixed-precision number formatting — the SSR/hydration determinism guard. */
const q = (n: number): number => Math.round(n * 100) / 100;

export default function CoreVisualization({ zorq, health, size = 320, busy = false }: Props) {
  const S = q(size);
  const cx = q(S / 2);
  const cy = cx;
  const R_OUTER = q(S * 0.46);      // five-plane segmented ring
  const R_RUNTIME = q(S * 0.335);   // runtime state ring
  const R_GUIDE = q(S * 0.26);      // rotating fine guide ring
  const R_CORE = q(S * 0.16);       // core disc

  const planes = zorq?.planes ?? [];
  const unavailable = !zorq;
  const posture = providerPosture(health);

  // five equal segments with a small gap
  const seg = planes.length === 5 ? planes : Array.from({ length: 5 }, (_, i) => planes[i] ?? null);
  const arcLen = 360 / 5;
  const gap = 6;

  const runtimeItems = zorq
    ? [
        { label: "CONN", value: zorq.runtime.connection },
        { label: "SYNC", value: zorq.runtime.sync },
        { label: "VOICE", value: zorq.runtime.voice.state },
        { label: "M-MODAL", value: zorq.runtime.multimodal.state },
      ]
    : [];

  const nodes = [
    { label: "MEMORY//OS", value: zorq ? "CANONICAL" : "UNKNOWN", tone: zorq ? "var(--z-ok)" : "var(--z-unknown)" },
    { label: "PROVIDER", value: posture.state === "REAL" ? "REAL AGENT" : posture.state === "FALLBACK" ? "FALLBACK" : "UNKNOWN", tone: posture.state === "REAL" ? "var(--z-ok)" : posture.state === "FALLBACK" ? "var(--z-ink-2)" : "var(--z-unknown)" },
    { label: "AUDIT", value: zorq ? (zorq.core.audit_integrity ? "VERIFIED" : "UNKNOWN") : "UNKNOWN", tone: zorq?.core.audit_integrity ? "var(--z-ok)" : "var(--z-unknown)" },
    { label: "CAPABILITIES", value: zorq ? String(zorq.capabilities_summary.count) : "—", tone: zorq ? "var(--z-ink-2)" : "var(--z-unknown)" },
  ];

  return (
    <div className="z-coreviz" role="img"
         aria-label={`ZORQ core instrument. ${unavailable ? "State unavailable" : `Connection ${zorq.runtime.connection}. Five planes: ${planes.map((p) => `${p.plane} ${p.status}`).join(", ")}. Provider ${posture.label}.`}`}>
      <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} style={{ display: "block", margin: "0 auto" }}>
        <defs>
          <clipPath id="coreclip"><circle cx={cx} cy={cy} r={q(R_CORE - 2)} /></clipPath>
        </defs>

        {/* crosshair structure */}
        <line x1={cx} y1={q(S * 0.03)} x2={cx} y2={q(S * 0.1)} stroke="var(--z-line-strong)" strokeWidth="1" />
        <line x1={cx} y1={q(S * 0.9)} x2={cx} y2={q(S * 0.97)} stroke="var(--z-line-strong)" strokeWidth="1" />
        <line x1={q(S * 0.03)} y1={cy} x2={q(S * 0.1)} y2={cy} stroke="var(--z-line-strong)" strokeWidth="1" />
        <line x1={q(S * 0.9)} y1={cy} x2={q(S * 0.97)} y2={cy} stroke="var(--z-line-strong)" strokeWidth="1" />

        {/* outer segmented ring: the five planes (real statuses) */}
        <g transform={`rotate(-90 ${cx} ${cy})`}>
          {seg.map((plane, i) => {
            const start = i * arcLen + gap / 2;
            const end = (i + 1) * arcLen - gap / 2;
            const r = R_OUTER;
            const x1 = q(cx + r * Math.cos((start * Math.PI) / 180));
            const y1 = q(cy + r * Math.sin((start * Math.PI) / 180));
            const x2 = q(cx + r * Math.cos((end * Math.PI) / 180));
            const y2 = q(cy + r * Math.sin((end * Math.PI) / 180));
            const t = plane ? planeSegmentTone(plane.status) : { stroke: "var(--z-ink-3)", opacity: 0.35, dash: "2 6" };
            return (
              <path key={i}
                d={`M ${x1} ${y1} A ${r} ${r} 0 ${arcLen > 180 ? 1 : 0} 1 ${x2} ${y2}`}
                fill="none" stroke={t.stroke} strokeWidth={q(S * 0.022)}
                strokeDasharray={t.dash} opacity={t.opacity}
                strokeLinecap="butt">
                <title>{plane ? `${plane.plane} — ${plane.status}${plane.status_note ? `: ${plane.status_note}` : ""}` : "No plane data"}</title>
              </path>
            );
          })}
        </g>

        {/* plane tick labels (outside the ring, upright) */}
        {seg.map((plane, i) => {
          const mid = ((i + 0.5) * arcLen - 90) * (Math.PI / 180);
          const lx = q(cx + q(R_OUTER + S * 0.055) * Math.cos(mid));
          const ly = q(cy + q(R_OUTER + S * 0.055) * Math.sin(mid));
          return (
            <text key={i} x={lx} y={ly} textAnchor="middle" dominantBaseline="middle"
                  style={{ fontFamily: "var(--mono)", fontSize: q(S * 0.032), letterSpacing: "0.14em", fill: "var(--z-ink-3)" }}>
              {plane ? plane.plane.toUpperCase().slice(0, 5) : "—"}
            </text>
          );
        })}

        {/* runtime state ring: four real states as arc ticks */}
        <g transform={`rotate(-90 ${cx} ${cy})`}>
          {runtimeItems.map((item, i) => {
            const start = i * 90 + 8;
            const end = (i + 1) * 90 - 8;
            const r = R_RUNTIME;
            const x1 = q(cx + r * Math.cos((start * Math.PI) / 180));
            const y1 = q(cy + r * Math.sin((start * Math.PI) / 180));
            const x2 = q(cx + r * Math.cos((end * Math.PI) / 180));
            const y2 = q(cy + r * Math.sin((end * Math.PI) / 180));
            return (
              <path key={item.label}
                d={`M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`}
                fill="none" stroke={runtimeTone(item.value)} strokeWidth={q(S * 0.014)} opacity={0.85}>
                <title>{`${item.label}: ${item.value}`}</title>
              </path>
            );
          })}
        </g>
        {!zorq && (
          <circle cx={cx} cy={cy} r={R_RUNTIME} fill="none" stroke="var(--z-ink-3)"
                  strokeWidth={q(S * 0.012)} strokeDasharray="3 7" opacity={0.5} />
        )}

        {/* fine rotating guide ring — structural motion only */}
        <g className="z-coreviz-rotate" style={{ transformOrigin: `${cx}px ${cy}px` }}>
          <circle cx={cx} cy={cy} r={R_GUIDE} fill="none" stroke="var(--z-line-strong)"
                  strokeWidth="1" strokeDasharray="1 9" opacity={0.8} />
          <circle cx={q(cx + R_GUIDE)} cy={cy} r={q(S * 0.008)} fill="var(--z-accent)" opacity={0.9} />
        </g>

        {/* orbit nodes — real subsystem values */}
        {nodes.map((node, i) => {
          const angle = ((i * 90 + 45 - 90) * Math.PI) / 180;
          const nx = q(cx + R_RUNTIME * Math.cos(angle));
          const ny = q(cy + R_RUNTIME * Math.sin(angle));
          return (
            <g key={node.label}>
              <line x1={q(cx + R_CORE * Math.cos(angle))} y1={q(cy + R_CORE * Math.sin(angle))}
                    x2={nx} y2={ny} stroke="var(--z-line)" strokeWidth="1" />
              <circle cx={nx} cy={ny} r={q(S * 0.014)} fill="var(--z-surface-1)" stroke={node.tone} strokeWidth="1.5">
                <title>{`${node.label}: ${node.value}`}</title>
              </circle>
              <text x={nx} y={q(ny + S * 0.052)} textAnchor="middle"
                    style={{ fontFamily: "var(--mono)", fontSize: q(S * 0.028), letterSpacing: "0.12em", fill: "var(--z-ink-3)" }}>
                {node.label}
              </text>
            </g>
          );
        })}

        {/* core */}
        <circle cx={cx} cy={cy} r={R_CORE} fill="var(--z-surface-2)" stroke="var(--z-accent-line)" strokeWidth="1" />
        <circle cx={cx} cy={cy} r={R_CORE} fill="none" stroke="var(--z-accent)" strokeWidth="1"
                strokeDasharray="2 4" opacity={busy ? 0.9 : 0.45}
                className={busy ? "z-coreviz-pulse" : undefined} />
        <g clipPath="url(#coreclip)">
          <text x={cx} y={q(cy - S * 0.022)} textAnchor="middle"
                style={{ fontFamily: "var(--mono)", fontWeight: 600, fontSize: q(S * 0.085), letterSpacing: "0.22em", fill: "var(--z-ink)" }}>
            ZORQ
          </text>
          <text x={cx} y={q(cy + S * 0.05)} textAnchor="middle"
                style={{ fontFamily: "var(--mono)", fontSize: q(S * 0.033), letterSpacing: "0.18em", fill: unavailable ? "var(--z-unknown)" : "var(--z-ok)" }}>
            {unavailable ? "UNKNOWN" : zorq.runtime.connection}
          </text>
        </g>
      </svg>

      {/* honest legend — real values only, no invented metrics */}
      <div className="z-coreviz-legend" aria-hidden="true">
        <span><i style={{ background: "var(--z-ok)" }} />established</span>
        <span><i style={{ background: "var(--z-ok)", opacity: 0.55 }} />partial</span>
        <span><i style={{ background: "var(--z-ink-3)" }} />designed</span>
        <span><i style={{ background: "var(--z-accent)" }} />active</span>
        {health && <span className="z-coreviz-count">{health.memory.count} MEMORIES</span>}
      </div>
    </div>
  );
}
