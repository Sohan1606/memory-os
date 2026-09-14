/**
 * Generates the MEMORY//OS scroll-sequence frames as local WebP assets.
 *
 * The sequence tells one story: a sealed memory core -> internal layers
 * separating -> signals and relationships emerging -> a fully exploded memory
 * architecture. Camera, lighting, background and framing are LOCKED across all
 * frames (only the explode factor animates), which is what keeps the
 * scroll-linked playback free of jitter and warping.
 *
 * Frames are pure deterministic SVG rasterised with sharp - no remote image
 * service is involved at build time or at runtime.
 *
 *   npm run frames
 */
import { mkdir, writeFile, readdir, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, "..", "public", "frames");

const FRAME_COUNT = 60;
const W = 1600;
const H = 900;

// Locked camera / composition constants - never animate these.
const CX = W / 2;
const CY = H / 2;
const BG = "#07070a";
const ACCENT = "#6ee7d7";
const WARM = "#f4f1ea";

const rnd = (seed) => {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
};

/** Deterministic node layout, identical in every frame. */
function buildNodes() {
  const r = rnd(20240917);
  const nodes = [];
  const rings = [
    { count: 6, radius: 70, layer: 0 },
    { count: 10, radius: 140, layer: 1 },
    { count: 14, radius: 215, layer: 2 },
    { count: 18, radius: 300, layer: 3 },
  ];
  rings.forEach((ring, ri) => {
    for (let i = 0; i < ring.count; i++) {
      const a = (i / ring.count) * Math.PI * 2 + ri * 0.35;
      nodes.push({
        angle: a,
        radius: ring.radius,
        layer: ring.layer,
        size: 2 + r() * 3.4,
        tilt: 0.42 + r() * 0.12,
        phase: r() * Math.PI * 2,
      });
    }
  });
  return nodes;
}

function buildParticles() {
  const r = rnd(777);
  return Array.from({ length: 150 }, () => ({
    a: r() * Math.PI * 2,
    d: 60 + r() * 420,
    s: 0.5 + r() * 1.3,
    o: 0.1 + r() * 0.4,
    tilt: 0.45,
  }));
}

const NODES = buildNodes();
const PARTICLES = buildParticles();

/** Project a ring point into the locked isometric-ish camera. */
function project(angle, radius, layer, explode, tilt) {
  const spin = explode * 0.55;              // gentle, shared rotation
  const a = angle + spin;
  const spread = 1 + explode * 0.95;        // layers push outward
  const lift = (layer - 1.5) * explode * 78; // layers separate vertically
  return {
    x: CX + Math.cos(a) * radius * spread,
    y: CY + Math.sin(a) * radius * spread * tilt + lift,
    depth: (Math.sin(a) + 1) / 2,
  };
}

function frameSVG(t) {
  // t: 0 -> 1 across the whole sequence
  const ease = t * t * (3 - 2 * t); // smoothstep
  const explode = ease;
  const parts = [];

  parts.push(`<rect width="${W}" height="${H}" fill="${BG}"/>`);
  parts.push(`<rect width="${W}" height="${H}" fill="url(#vignette)"/>`);

  // faint reference grid, fades in as structure is revealed
  const gridO = 0.05 + explode * 0.07;
  parts.push(`<g opacity="${gridO.toFixed(3)}" stroke="${ACCENT}" stroke-width="0.5">`);
  for (let i = 1; i < 16; i++) {
    const x = (W / 16) * i;
    parts.push(`<line x1="${x}" y1="0" x2="${x}" y2="${H}"/>`);
  }
  for (let i = 1; i < 9; i++) {
    const y = (H / 9) * i;
    parts.push(`<line x1="0" y1="${y}" x2="${W}" y2="${y}"/>`);
  }
  parts.push(`</g>`);

  // ambient particles
  parts.push(`<g>`);
  for (const p of PARTICLES) {
    const a = p.a + explode * 0.3;
    const d = p.d * (1 + explode * 0.3);
    const x = CX + Math.cos(a) * d;
    const y = CY + Math.sin(a) * d * p.tilt;
    const o = p.o * (0.35 + explode * 0.65);
    parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${p.s.toFixed(2)}" fill="${WARM}" opacity="${o.toFixed(3)}"/>`);
  }
  parts.push(`</g>`);

  const projected = NODES.map((n) => ({
    ...n,
    ...project(n.angle, n.radius, n.layer, explode, n.tilt),
  }));

  // relationship edges appear progressively (signal -> relationship)
  const edgeStrength = Math.max(0, (explode - 0.18) / 0.82);
  if (edgeStrength > 0) {
    parts.push(`<g stroke="${ACCENT}" fill="none">`);
    for (let i = 0; i < projected.length; i++) {
      const a = projected[i];
      for (let j = i + 1; j < projected.length; j++) {
        const b = projected[j];
        if (Math.abs(a.layer - b.layer) > 1) continue;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist = Math.hypot(dx, dy);
        if (dist > 165) continue;
        const o = edgeStrength * (1 - dist / 165) * 0.42;
        if (o < 0.02) continue;
        parts.push(`<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" stroke-width="${(0.55 + o).toFixed(2)}" opacity="${o.toFixed(3)}"/>`);
      }
    }
    parts.push(`</g>`);
  }

  // translucent layer shells separating outward
  for (let layer = 3; layer >= 0; layer--) {
    const radius = [70, 140, 215, 300][layer] * (1 + explode * 0.95);
    const lift = (layer - 1.5) * explode * 78;
    const o = 0.06 + explode * 0.1;
    parts.push(`<ellipse cx="${CX}" cy="${(CY + lift).toFixed(1)}" rx="${radius.toFixed(1)}" ry="${(radius * 0.45).toFixed(1)}" fill="none" stroke="${WARM}" stroke-width="0.9" opacity="${o.toFixed(3)}"/>`);
  }

  // the nucleus - contracts in brightness as the system expands around it
  const coreR = 52 * (1 - explode * 0.35);
  parts.push(`<circle cx="${CX}" cy="${CY}" r="${(coreR * 2.6).toFixed(1)}" fill="url(#coreGlow)" opacity="${(0.5 - explode * 0.2).toFixed(3)}"/>`);
  parts.push(`<circle cx="${CX}" cy="${CY}" r="${coreR.toFixed(1)}" fill="url(#coreFill)" stroke="${ACCENT}" stroke-width="1.1" opacity="0.92"/>`);
  parts.push(`<ellipse cx="${CX}" cy="${CY}" rx="${coreR.toFixed(1)}" ry="${(coreR * 0.38).toFixed(1)}" fill="none" stroke="${WARM}" stroke-width="0.7" opacity="0.35"/>`);

  // memory nodes, painted back-to-front for depth
  projected.sort((a, b) => a.depth - b.depth);
  for (const n of projected) {
    const bright = 0.3 + n.depth * 0.7;
    const r = n.size * (0.8 + n.depth * 0.5);
    const fill = n.layer % 2 === 0 ? ACCENT : WARM;
    parts.push(`<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r.toFixed(2)}" fill="${fill}" opacity="${(bright * (0.45 + explode * 0.5)).toFixed(3)}"/>`);
    if (n.depth > 0.72 && explode > 0.35) {
      parts.push(`<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${(r * 3.1).toFixed(2)}" fill="${fill}" opacity="${(0.07 * explode).toFixed(3)}"/>`);
    }
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <defs>
    <radialGradient id="coreGlow" cx="50%" cy="50%">
      <stop offset="0%" stop-color="${ACCENT}" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="${ACCENT}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="coreFill" cx="38%" cy="34%">
      <stop offset="0%" stop-color="#dffaf4" stop-opacity="0.95"/>
      <stop offset="55%" stop-color="#1d4f4a" stop-opacity="0.75"/>
      <stop offset="100%" stop-color="#0a1414" stop-opacity="0.9"/>
    </radialGradient>
    <radialGradient id="vignette" cx="50%" cy="50%">
      <stop offset="55%" stop-color="#000000" stop-opacity="0"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0.75"/>
    </radialGradient>
  </defs>
  ${parts.join("\n  ")}
</svg>`;
}

async function main() {
  await mkdir(OUT, { recursive: true });
  if (existsSync(OUT)) {
    for (const f of await readdir(OUT)) {
      if (f.endsWith(".webp")) await unlink(path.join(OUT, f));
    }
  }
  for (let i = 0; i < FRAME_COUNT; i++) {
    const t = FRAME_COUNT === 1 ? 0 : i / (FRAME_COUNT - 1);
    const svg = frameSVG(t);
    const name = `core_${String(i).padStart(3, "0")}.webp`;
    await sharp(Buffer.from(svg))
      .webp({ quality: 82, effort: 5 })
      .toFile(path.join(OUT, name));
  }
  await writeFile(
    path.join(OUT, "manifest.json"),
    JSON.stringify({ count: FRAME_COUNT, width: W, height: H,
      pattern: "core_%03d.webp", generated: "procedural-svg" }, null, 2)
  );
  console.log(`Generated ${FRAME_COUNT} frames in public/frames`);
}

main().catch((err) => { console.error(err); process.exit(1); });
