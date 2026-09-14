"use client";
/**
 * Voice -> memory. Speech (or typed text, when speech is unsupported) flows
 * through exactly the same memory-creation pathway as every other surface.
 */
import { useEffect, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";
import { useSpeech } from "@/hooks/useSpeech";
import { useReducedMotion } from "@/hooks/useReducedMotion";

type Stage = "IDLE" | "LISTENING" | "PROCESSING" | "MEMORY DETECTED" | "MEMORY SAVED" | "RESPONSE";

export default function VoiceDemo() {
  const { createMemory, health, select } = useMemoryStore();
  const speech = useSpeech();
  const reduced = useReducedMotion();

  const [stage, setStage] = useState<Stage>("IDLE");
  const [text, setText] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (speech.transcript) setText(speech.transcript);
  }, [speech.transcript]);

  useEffect(() => {
    if (speech.listening) setStage("LISTENING");
    else setStage((s) => (s === "LISTENING" ? "IDLE" : s));
  }, [speech.listening]);

  /* lightweight procedural waveform */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const render = (t: number) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      const active = stage === "LISTENING" || stage === "PROCESSING";
      const bars = 48;
      for (let i = 0; i < bars; i++) {
        const x = (i / bars) * w;
        const seedPhase = i * 0.55;
        const amp = active
          ? (Math.sin(t * 0.005 + seedPhase) * 0.5 + 0.5) * (0.3 + Math.sin(i * 0.3) * 0.25) + 0.1
          : 0.06;
        const bh = amp * h * 0.8;
        ctx.fillStyle = active ? "rgba(110,231,215,0.7)" : "rgba(244,241,234,0.18)";
        ctx.fillRect(x, (h - bh) / 2, Math.max(1, w / bars - 3), bh);
      }
      rafRef.current = requestAnimationFrame(render);
    };
    if (reduced) { render(0); }
    else rafRef.current = requestAnimationFrame(render);
    window.addEventListener("resize", resize, { passive: true });
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      window.removeEventListener("resize", resize);
    };
  }, [stage, reduced]);

  const submit = async () => {
    const content = text.trim();
    if (!content) { setError("Say or type something for the system to remember."); return; }
    setError(null);
    setResult(null);
    setStage("PROCESSING");
    try {
      setStage("MEMORY DETECTED");
      const res = await createMemory(content, undefined, "voice");
      setStage("MEMORY SAVED");
      const verb = res.action === "created" ? "Stored a new memory"
        : res.action === "reinforced" ? "Reinforced an existing memory"
        : "Updated a conflicting memory";
      setResult(`${verb}: “${res.memory.content}” · ${res.memory.category.replace(/_/g, " ")} · v${res.memory.version}`);
      select(res.memory.id);
      setStage("RESPONSE");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not store that memory.");
      setStage("IDLE");
    }
  };

  const stages: Stage[] = ["IDLE", "LISTENING", "PROCESSING", "MEMORY DETECTED", "MEMORY SAVED", "RESPONSE"];

  return (
    <div style={{ display: "grid", gap: "1.4rem" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {stages.map((s) => (
          <span key={s} className="mono" style={{
            padding: "0.35rem 0.65rem", fontSize: "0.5625rem", letterSpacing: "0.14em",
            border: `1px solid ${stage === s ? "var(--accent-line)" : "var(--line)"}`,
            color: stage === s ? "var(--accent)" : "var(--muted)",
            background: stage === s ? "var(--accent-dim)" : "transparent",
          }}>{s}</span>
        ))}
      </div>

      <canvas ref={canvasRef} aria-hidden="true"
              style={{ width: "100%", height: 90, display: "block" }} />

      <p className="mono" style={{ color: "var(--muted)" }}>
        {speech.supported
          ? "Browser speech recognition available."
          : "Browser speech recognition unavailable — text mode is fully supported."}
        {health?.voice?.mode === "whisper" && " Server-side Whisper is configured."}
      </p>

      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <label htmlFor="voice-text" className="sr-only">Memory to store</label>
        <input id="voice-text" className="field" value={text}
               onChange={(e) => setText(e.target.value)}
               placeholder="Remember that I prefer concise explanations."
               style={{ flex: "1 1 260px" }} />
        {speech.supported && (
          <button className="btn" onClick={() => (speech.listening ? speech.stop() : speech.start())}>
            {speech.listening ? "Stop" : "Speak"}
          </button>
        )}
        <button className="btn btn-primary" onClick={() => void submit()} data-cursor="cta">
          Store memory
        </button>
      </div>

      {speech.error && <p className="body" style={{ color: "#ff8a7a" }}>{speech.error}</p>}
      {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
      {result && (
        <div className="panel" style={{ padding: "1rem 1.2rem", borderColor: "var(--accent-line)" }}>
          <p className="label label-accent">Memory saved</p>
          <p className="body" style={{ color: "var(--warm)", marginTop: "0.4rem" }}>{result}</p>
        </div>
      )}
    </div>
  );
}
