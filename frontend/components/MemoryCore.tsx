"use client";
/**
 * Procedural memory core rendered on canvas.
 *
 * Performance-conscious by design: one RAF loop, paused when off-screen or
 * when the tab is hidden, DPR capped, and fully static under reduced motion.
 */
import { useEffect, useRef } from "react";

import { useReducedMotion } from "@/hooks/useReducedMotion";

interface Props { height?: string; intensity?: number; interactive?: boolean }

interface Node { angle: number; radius: number; layer: number; size: number; speed: number }

export default function MemoryCore({ height = "100svh", intensity = 1, interactive = true }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const pointer = useRef({ x: 0, y: 0, tx: 0, ty: 0 });
  const visible = useRef(true);
  const reduced = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // graceful: nothing renders, layout still intact

    const nodes: Node[] = [];
    let seed = 99;
    const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    const rings = [[6, 0.14], [10, 0.26], [14, 0.39], [16, 0.52]] as const;
    rings.forEach(([count, radius], layer) => {
      for (let i = 0; i < count; i++) {
        nodes.push({
          angle: (i / count) * Math.PI * 2 + layer * 0.4,
          radius, layer,
          size: 1 + rand() * 2.2,
          speed: 0.00006 + rand() * 0.00012,
        });
      }
    });
    const particles = Array.from({ length: 80 }, () => ({
      a: rand() * Math.PI * 2, d: 0.1 + rand() * 0.55,
      s: 0.4 + rand() * 1.1, o: 0.08 + rand() * 0.3,
    }));

    let dpr = 1;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    const onPointer = (e: PointerEvent) => {
      if (!interactive) return;
      const rect = canvas.getBoundingClientRect();
      pointer.current.tx = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
      pointer.current.ty = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
    };

    const render = (time: number) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const cx = w / 2;
      const cy = h / 2;
      const unit = Math.min(w, h);

      pointer.current.x += (pointer.current.tx - pointer.current.x) * 0.045;
      pointer.current.y += (pointer.current.ty - pointer.current.y) * 0.045;
      const px = pointer.current.x * unit * 0.035;
      const py = pointer.current.y * unit * 0.025;

      ctx.clearRect(0, 0, w, h);

      const pts = nodes.map((n) => {
        const a = n.angle + (reduced ? 0 : time * n.speed);
        const tilt = 0.42 + pointer.current.y * 0.08;
        return {
          x: cx + Math.cos(a) * n.radius * unit + px * (0.4 + n.layer * 0.22),
          y: cy + Math.sin(a) * n.radius * unit * tilt + py * (0.4 + n.layer * 0.22)
             + (n.layer - 1.5) * unit * 0.012,
          depth: (Math.sin(a) + 1) / 2,
          n,
        };
      });

      // ambient particles
      for (const p of particles) {
        const a = p.a + (reduced ? 0 : time * 0.00002);
        const x = cx + Math.cos(a) * p.d * unit + px * 0.3;
        const y = cy + Math.sin(a) * p.d * unit * 0.45 + py * 0.3;
        ctx.globalAlpha = p.o * intensity;
        ctx.fillStyle = "#f4f1ea";
        ctx.beginPath();
        ctx.arc(x, y, p.s, 0, Math.PI * 2);
        ctx.fill();
      }

      // relationship edges
      ctx.strokeStyle = "#6ee7d7";
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          if (Math.abs(pts[i].n.layer - pts[j].n.layer) > 1) continue;
          const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y);
          const max = unit * 0.16;
          if (d > max) continue;
          ctx.globalAlpha = (1 - d / max) * 0.2 * intensity;
          ctx.lineWidth = 0.6;
          ctx.beginPath();
          ctx.moveTo(pts[i].x, pts[i].y);
          ctx.lineTo(pts[j].x, pts[j].y);
          ctx.stroke();
        }
      }

      // nucleus
      const coreR = unit * 0.052;
      const glow = ctx.createRadialGradient(cx + px * 0.3, cy + py * 0.3, 0,
                                            cx + px * 0.3, cy + py * 0.3, coreR * 4);
      glow.addColorStop(0, "rgba(110,231,215,0.42)");
      glow.addColorStop(1, "rgba(110,231,215,0)");
      ctx.globalAlpha = intensity;
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx + px * 0.3, cy + py * 0.3, coreR * 4, 0, Math.PI * 2);
      ctx.fill();

      const body = ctx.createRadialGradient(
        cx - coreR * 0.35 + px * 0.3, cy - coreR * 0.4 + py * 0.3, 0,
        cx + px * 0.3, cy + py * 0.3, coreR);
      body.addColorStop(0, "rgba(223,250,244,0.95)");
      body.addColorStop(0.55, "rgba(29,79,74,0.8)");
      body.addColorStop(1, "rgba(10,20,20,0.92)");
      ctx.fillStyle = body;
      ctx.beginPath();
      ctx.arc(cx + px * 0.3, cy + py * 0.3, coreR, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(110,231,215,0.55)";
      ctx.lineWidth = 1;
      ctx.stroke();

      // nodes, back to front
      pts.sort((a, b) => a.depth - b.depth);
      for (const p of pts) {
        ctx.globalAlpha = (0.25 + p.depth * 0.7) * intensity;
        ctx.fillStyle = p.n.layer % 2 === 0 ? "#6ee7d7" : "#f4f1ea";
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.n.size * (0.75 + p.depth * 0.5), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    const loop = (t: number) => {
      if (visible.current) render(t);
      rafRef.current = requestAnimationFrame(loop);
    };

    if (reduced) {
      render(0);
    } else {
      rafRef.current = requestAnimationFrame(loop);
    }

    const io = new IntersectionObserver(([e]) => { visible.current = e.isIntersecting; });
    io.observe(canvas);
    const onVisibility = () => { visible.current = !document.hidden; };

    window.addEventListener("resize", resize, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    if (interactive) window.addEventListener("pointermove", onPointer, { passive: true });

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      io.disconnect();
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
    };
  }, [reduced, intensity, interactive]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{ display: "block", width: "100%", height, pointerEvents: "none" }}
    />
  );
}
