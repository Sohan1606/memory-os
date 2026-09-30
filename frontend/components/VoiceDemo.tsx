"use client";
/**
 * Voice -> memory. Speech (or typed text, when speech is unsupported) flows
 * through exactly the same memory-creation pathway as every other surface.
 *
 * Phase 3F-min: driven by the real voice state machine (useVoice). The state
 * chips show actual machine states — nothing simulated — plus the two memory
 * phases of this demo's own store call. Voice input here is a draft the user
 * can edit before storing; nothing is persisted until "Store memory".
 */
import { useEffect, useRef, useState } from "react";

import { useMemoryStore } from "@/hooks/useMemoryStore";
import { useVoice } from "@/hooks/useVoice";
import { useReducedMotion } from "@/hooks/useReducedMotion";

type MemoryPhase = "NONE" | "STORING" | "SAVED";

const VOICE_CHIPS = [
  "IDLE", "REQUESTING_PERMISSION", "LISTENING", "TRANSCRIBING", "ERROR",
] as const;

export default function VoiceDemo() {
  const { createMemory, health, select } = useMemoryStore();
  const voice = useVoice();
  const reduced = useReducedMotion();

  const [text, setText] = useState("");
  const [memoryPhase, setMemoryPhase] = useState<MemoryPhase>("NONE");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);

  // Finalized transcript becomes an editable draft (never auto-stored).
  useEffect(() => {
    if (voice.finalTranscript) {
      setText(voice.finalTranscript);
      voice.clearDraft();
    }
  }, [voice, voice.finalTranscript]);

  /* lightweight procedural waveform — decorative only (aria-hidden) */
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
    const active = voice.state === "LISTENING" || voice.state === "TRANSCRIBING"
      || memoryPhase === "STORING";
    const render = (t: number) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
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
  }, [voice.state, memoryPhase, reduced]);

  const submit = async () => {
    const content = text.trim();
    if (!content) { setError("Say or type something for the system to remember."); return; }
    setError(null);
    setResult(null);
    setMemoryPhase("STORING");
    try {
      const res = await createMemory(content, undefined, "voice");
      setMemoryPhase("SAVED");
      const verb = res.action === "created" ? "Stored a new memory"
        : res.action === "reinforced" ? "Reinforced an existing memory"
        : "Updated a conflicting memory";
      setResult(`${verb}: “${res.memory.content}” · ${res.memory.category.replace(/_/g, " ")} · v${res.memory.version}`);
      select(res.memory.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not store that memory.");
      setMemoryPhase("NONE");
    }
  };

  return (
    <div style={{ display: "grid", gap: "1.4rem" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}
           aria-label="Voice transport state">
        {VOICE_CHIPS.map((s) => (
          <span key={s} className="mono" style={{
            padding: "0.35rem 0.65rem", fontSize: "0.5625rem", letterSpacing: "0.14em",
            border: `1px solid ${voice.state === s ? "var(--accent-line)" : "var(--line)"}`,
            color: voice.state === s ? "var(--accent)" : "var(--muted)",
            background: voice.state === s ? "var(--accent-dim)" : "transparent",
          }}>{s.replace(/_/g, " ")}</span>
        ))}
        {(["STORING", "SAVED"] as const).map((s) => (
          <span key={s} className="mono" style={{
            padding: "0.35rem 0.65rem", fontSize: "0.5625rem", letterSpacing: "0.14em",
            border: `1px solid ${memoryPhase === s ? "var(--accent-line)" : "var(--line)"}`,
            color: memoryPhase === s ? "var(--accent)" : "var(--muted)",
            background: memoryPhase === s ? "var(--accent-dim)" : "transparent",
          }}>MEMORY {s}</span>
        ))}
      </div>

      <canvas ref={canvasRef} aria-hidden="true"
              style={{ width: "100%", height: 90, display: "block" }} />

      <p className="mono" style={{ color: "var(--muted)" }}>
        {voice.supported
          ? `Browser speech recognition available (${voice.language}). ${voice.sttDisclosure}`
          : "Browser speech recognition unavailable — text mode is fully supported."}
        {health?.voice?.mode === "whisper" && " Server-side local Whisper is configured."}
      </p>

      {(voice.state === "LISTENING" || voice.state === "TRANSCRIBING") && (
        <p className="mono" style={{ color: "var(--muted)", margin: 0 }}>
          {voice.state === "LISTENING" ? "● REC" : "◈ FINALIZING"} · INTERIM — NOT STORED
          {voice.interimTranscript ? ` · ${voice.interimTranscript}` : ""}
        </p>
      )}

      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <label htmlFor="voice-text" className="sr-only">Memory to store</label>
        <input id="voice-text" className="field" value={text}
               onChange={(e) => setText(e.target.value)}
               placeholder="Remember that I prefer concise explanations."
               style={{ flex: "1 1 260px" }} />
        {voice.supported && (
          <button className="btn" type="button"
                  aria-pressed={voice.state === "LISTENING"}
                  onClick={() => {
                    if (voice.state === "LISTENING") voice.stopListening();
                    else if (voice.state === "REQUESTING_PERMISSION") voice.cancelListening();
                    else if (voice.state === "IDLE" || voice.state === "ERROR") voice.startListening();
                  }}>
            {voice.state === "LISTENING" ? "Stop"
              : voice.state === "REQUESTING_PERMISSION" ? "Cancel"
              : "Speak"}
          </button>
        )}
        <button className="btn btn-primary" onClick={() => void submit()} data-cursor="cta">
          Store memory
        </button>
      </div>

      {voice.error && (
        <p className="body" style={{ color: "#ff8a7a", display: "flex", gap: "0.6rem",
                                     alignItems: "center", flexWrap: "wrap" }}>
          {voice.error}
          <button className="btn" type="button" onClick={() => voice.acknowledgeError()}>
            Dismiss
          </button>
        </p>
      )}
      {error && <p className="body" style={{ color: "#ff8a7a" }}>{error}</p>}
      {result && (
        <div className="panel" style={{ padding: "1rem 1.2rem", borderColor: "var(--accent-line)" }}>
          <p className="label label-accent">Memory saved</p>
          <p className="body" style={{ color: "var(--warm)", marginTop: "0.4rem" }}>{result}</p>
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">
        {voice.state === "LISTENING" ? "Microphone listening."
          : voice.state === "TRANSCRIBING" ? "Finalizing transcript."
          : memoryPhase === "SAVED" ? "Memory saved."
          : ""}
      </span>
    </div>
  );
}
