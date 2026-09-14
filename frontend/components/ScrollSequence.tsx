"use client";
/**
 * Scroll-linked image sequence.
 *
 * Technique (per the reference brief):
 *  - still WebP frames painted into a single <canvas>, never <img> tags
 *  - frames preloaded with visible progress before playback becomes live
 *  - a tall scroll container with a sticky full-viewport canvas
 *  - scroll progress (0..1) mapped to a clamped frame index
 *  - requestAnimationFrame scheduling; the canvas repaints ONLY when the
 *    frame index actually changes
 *  - reduced frame subset below 768px
 *  - a single static frame when prefers-reduced-motion is set
 *  - IntersectionObserver pauses work while the section is off-screen
 *
 * Tuning: raise SECTION_VH to slow the sequence down, lower it to speed up.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useReducedMotion } from "@/hooks/useReducedMotion";

const FRAME_COUNT = 60;
const FRAME_W = 1600;
const FRAME_H = 900;
/** Height of the scroll container. Tune pacing here. */
const SECTION_VH = 400;
const STATIC_FRAME = 34;
const MAX_DPR = 2;

const framePath = (i: number) =>
  `/frames/core_${String(i).padStart(3, "0")}.webp`;

interface Caption { at: number; label: string; text: string }

const CAPTIONS: Caption[] = [
  { at: 0.0, label: "01 — SIGNAL", text: "A sealed memory core." },
  { at: 0.26, label: "02 — CONTEXT", text: "Internal layers separate." },
  { at: 0.5, label: "03 — MEMORY", text: "Fragments resolve into memories." },
  { at: 0.72, label: "04 — RELATIONSHIP", text: "Memories discover each other." },
  { at: 0.9, label: "05 — SYSTEM", text: "A memory architecture." },
];

export default function ScrollSequence() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imagesRef = useRef<(HTMLImageElement | null)[]>([]);
  const rafRef = useRef<number | null>(null);
  const currentFrame = useRef<number>(-1);
  const visibleRef = useRef(true);

  const reducedMotion = useReducedMotion();
  const isMobile = useMediaQuery("(max-width: 767px)");

  const [progress, setProgress] = useState(0);
  const [loaded, setLoaded] = useState(0);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  // Mobile loads every second frame; desktop loads the full set.
  const step = isMobile ? 2 : 1;
  const indices = useRef<number[]>([]);
  if (indices.current.length === 0 || indices.current[1] !== step) {
    const list: number[] = [];
    for (let i = 0; i < FRAME_COUNT; i += step) list.push(i);
    if (list[list.length - 1] !== FRAME_COUNT - 1) list.push(FRAME_COUNT - 1);
    indices.current = list;
  }

  /* ------------------------------------------------------------ preload */
  useEffect(() => {
    let cancelled = false;
    const list = reducedMotion ? [STATIC_FRAME] : indices.current;
    const images: (HTMLImageElement | null)[] = new Array(FRAME_COUNT).fill(null);
    imagesRef.current = images;
    let done = 0;
    let errors = 0;

    list.forEach((idx) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => {
        if (cancelled) return;
        images[idx] = img;
        done += 1;
        setLoaded(done / list.length);
        if (done + errors >= list.length) setReady(true);
      };
      img.onerror = () => {
        if (cancelled) return;
        // A missing frame must never break the sequence: we simply hold the
        // previous available frame at that index.
        errors += 1;
        if (errors >= list.length) setFailed(true);
        if (done + errors >= list.length) setReady(true);
      };
      img.src = framePath(idx);
    });

    return () => {
      cancelled = true;
      images.forEach((img) => { if (img) { img.onload = null; img.onerror = null; } });
      imagesRef.current = [];
    };
  }, [reducedMotion, step]);

  /* ------------------------------------------------------------- drawing */
  const draw = useCallback((frameIndex: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // hold the nearest previously-loaded frame if this one is unavailable
    let img = imagesRef.current[frameIndex] ?? null;
    if (!img) {
      for (let d = 1; d < FRAME_COUNT && !img; d++) {
        img = imagesRef.current[frameIndex - d] ?? imagesRef.current[frameIndex + d] ?? null;
      }
    }
    if (!img) return;

    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    ctx.clearRect(0, 0, cw, ch);

    // preserve aspect ratio (cover), never stretch
    const scale = Math.max(cw / FRAME_W, ch / FRAME_H);
    const w = FRAME_W * scale;
    const h = FRAME_H * scale;
    ctx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
  }, []);

  const resize = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (currentFrame.current >= 0) draw(currentFrame.current);
  }, [draw]);

  useEffect(() => {
    if (!ready) return;
    resize();
    window.addEventListener("resize", resize, { passive: true });
    window.addEventListener("orientationchange", resize, { passive: true });
    return () => {
      window.removeEventListener("resize", resize);
      window.removeEventListener("orientationchange", resize);
    };
  }, [ready, resize]);

  /* ------------------------------------- reduced motion: one static frame */
  useEffect(() => {
    if (!ready || !reducedMotion) return;
    currentFrame.current = STATIC_FRAME;
    resize();
    draw(STATIC_FRAME);
    setProgress(STATIC_FRAME / (FRAME_COUNT - 1));
  }, [ready, reducedMotion, draw, resize]);

  /* -------------------------------------------- scroll -> frame (via RAF) */
  useEffect(() => {
    if (!ready || reducedMotion) return;

    const wrap = wrapRef.current;
    if (!wrap) return;

    const observer = new IntersectionObserver(
      ([entry]) => { visibleRef.current = entry.isIntersecting; },
      { rootMargin: "200px 0px" });
    observer.observe(wrap);

    const compute = () => {
      rafRef.current = null;
      if (!visibleRef.current) return;
      const rect = wrap.getBoundingClientRect();
      const scrollable = rect.height - window.innerHeight;
      if (scrollable <= 0) return;
      // normalised scroll progress through the tall section: 0 -> 1
      const raw = -rect.top / scrollable;
      const p = Math.min(1, Math.max(0, raw));
      // map progress to a clamped frame index
      const frame = Math.min(FRAME_COUNT - 1, Math.max(0, Math.floor(p * (FRAME_COUNT - 1))));
      setProgress(p);
      // repaint ONLY when the frame actually changes
      if (frame !== currentFrame.current) {
        currentFrame.current = frame;
        draw(frame);
      }
    };

    const onScroll = () => {
      if (rafRef.current === null) rafRef.current = requestAnimationFrame(compute);
    };

    compute();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      observer.disconnect();
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [ready, reducedMotion, draw]);

  const caption = [...CAPTIONS].reverse().find((c) => progress >= c.at) ?? CAPTIONS[0];
  const sectionHeight = reducedMotion ? "100svh" : `${SECTION_VH}vh`;

  return (
    <section
      id="product"
      ref={wrapRef}
      aria-label="Memory core assembly sequence"
      style={{ position: "relative", height: sectionHeight, background: "var(--black)" }}
    >
      <div style={{ position: "sticky", top: 0, height: "100svh", overflow: "hidden" }}>
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="An abstract memory core separating into a network of connected memory nodes."
          style={{ display: "block", width: "100%", height: "100%" }}
        />

        {/* preload state */}
        {!ready && (
          <div style={overlayCenter} aria-live="polite">
            <p className="label label-accent" style={{ marginBottom: "1rem" }}>
              Initializing memory core
            </p>
            <div style={{ width: "min(240px, 60vw)", height: 1, background: "var(--line)" }}>
              <div style={{
                width: `${Math.round(loaded * 100)}%`, height: "100%",
                background: "var(--accent)", transition: "width .3s var(--ease)",
              }} />
            </div>
            <p className="mono" style={{ color: "var(--muted)", marginTop: "0.75rem" }}>
              {Math.round(loaded * 100)}%
            </p>
          </div>
        )}

        {failed && ready && (
          <div style={overlayCenter}>
            <p className="body" style={{ maxWidth: 380, textAlign: "center" }}>
              The frame sequence could not load. The rest of the experience is unaffected.
            </p>
          </div>
        )}

        {/* caption + progress */}
        {ready && !failed && (
          <>
            <div style={{
              position: "absolute", left: "var(--pad)", bottom: "clamp(2rem,8vh,5rem)",
              maxWidth: "min(420px, 80vw)", pointerEvents: "none",
            }}>
              <p className="label label-accent" style={{ marginBottom: "0.6rem" }}>
                {caption.label}
              </p>
              <p className="subhead" style={{ color: "var(--warm)" }}>{caption.text}</p>
            </div>

            <div style={{
              position: "absolute", right: "var(--pad)", bottom: "clamp(2rem,8vh,5rem)",
              display: "flex", alignItems: "center", gap: "0.9rem", pointerEvents: "none",
            }}>
              <span className="mono" style={{ color: "var(--muted)" }}>
                {String(Math.round(progress * 100)).padStart(3, "0")}
              </span>
              <div style={{ width: "clamp(60px,12vw,140px)", height: 1, background: "var(--line)" }}>
                <div style={{ width: `${progress * 100}%`, height: "100%", background: "var(--accent)" }} />
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

const overlayCenter: React.CSSProperties = {
  position: "absolute", inset: 0, display: "flex", flexDirection: "column",
  alignItems: "center", justifyContent: "center", gap: "0.25rem",
};
