"use client";
/**
 * ZORQ Phase 3F speech-output transport (3F-min).
 *
 * Replaces the Z-UI.1 fire-and-forget `speechSynthesis.speak()` with a fully
 * tracked utterance lifecycle:
 *
 *   pending → speaking (real onstart) → completed | cancelled | error
 *
 * Truthfulness rules (spec §9):
 *  - SO2: `speaking` is only reported on real `onstart` evidence.
 *  - SO5: a stale onend/onerror from a superseded or cancelled utterance can
 *    never mutate newer state (per-utterance generation guard).
 *  - SO4: synthesis is cancelled on unmount and before any new utterance.
 *  - Output is optional: when `speechSynthesis` is absent, `speak()` returns
 *    false and nothing else happens — the rendered text is the answer.
 *
 * This hook consumes the exact final response text rendered by Chat; it never
 * generates spoken-only content. It carries no authority of any kind.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type SpeechOutputStatus = "idle" | "pending" | "speaking";
export type SpeechOutputOutcome =
  | "none" | "completed" | "cancelled" | "error" | "unavailable";

export interface SpeechOutputEvents {
  /** Real utterance start (onstart fired). */
  onStart?: () => void;
  /** Natural completion (onend of a non-cancelled utterance). */
  onComplete?: () => void;
  /** Explicit cancellation confirmed (user stop / barge-in / cleanup). */
  onCancelled?: () => void;
  /** Utterance error. `startedBefore` tells whether audio had begun. */
  onError?: (startedBefore: boolean) => void;
}

export interface SpeechOutput {
  /** Whether window.speechSynthesis exists in this browser. */
  available: boolean;
  status: SpeechOutputStatus;
  lastOutcome: SpeechOutputOutcome;
  /**
   * Speak `text`. Returns false (outcome "unavailable") when synthesis is
   * absent — callers must treat that as a silent, lossless degradation.
   */
  speak: (text: string, events?: SpeechOutputEvents) => boolean;
  /** Cancel the current utterance (idempotent; safe in any status). */
  cancel: () => void;
}

interface ActiveUtterance {
  generation: number;
  cancelled: boolean;
  started: boolean;
  events?: SpeechOutputEvents;
}

export function useSpeechOutput(): SpeechOutput {
  const [available, setAvailable] = useState(false);
  const [status, setStatus] = useState<SpeechOutputStatus>("idle");
  const [lastOutcome, setLastOutcome] = useState<SpeechOutputOutcome>("none");
  const activeRef = useRef<ActiveUtterance | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    setAvailable(typeof window !== "undefined" && "speechSynthesis" in window);
  }, []);

  const cancel = useCallback(() => {
    const active = activeRef.current;
    if (active && !active.cancelled) {
      active.cancelled = true;
      try { window.speechSynthesis.cancel(); } catch { /* already stopped */ }
      setStatus("idle");
      setLastOutcome("cancelled");
      active.events?.onCancelled?.();
      activeRef.current = null;
    } else if (typeof window !== "undefined" && "speechSynthesis" in window) {
      // No tracked utterance: still flush any queue defensively (idempotent).
      try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    }
  }, []);

  const speak = useCallback((text: string, events?: SpeechOutputEvents): boolean => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setLastOutcome("unavailable");
      return false;
    }
    // SO4: flush anything previous before a new utterance.
    cancel();

    const generation = ++generationRef.current;
    const record: ActiveUtterance = { generation, cancelled: false, started: false, events };
    activeRef.current = record;
    setStatus("pending");

    const utterance = new SpeechSynthesisUtterance(text);
    const isCurrent = () =>
      activeRef.current === record && record.generation === generationRef.current;

    utterance.onstart = () => {
      // SO5: stale/cancelled utterances cannot claim SPEAKING.
      if (!isCurrent() || record.cancelled) return;
      record.started = true;
      setStatus("speaking");
      events?.onStart?.();
    };
    utterance.onend = () => {
      // A cancelled utterance also fires onend in some engines: cancellation
      // was already reported by cancel(); never double-report as completion.
      if (record.cancelled) return;
      if (!isCurrent()) return; // stale completion (SO5 / spec test 19)
      activeRef.current = null;
      setStatus("idle");
      setLastOutcome("completed");
      events?.onComplete?.();
    };
    utterance.onerror = () => {
      if (record.cancelled) return; // engines report cancel as an error event
      if (!isCurrent()) return;
      activeRef.current = null;
      setStatus("idle");
      setLastOutcome("error");
      events?.onError?.(record.started);
    };

    try {
      window.speechSynthesis.speak(utterance);
    } catch {
      activeRef.current = null;
      setStatus("idle");
      setLastOutcome("error");
      events?.onError?.(false);
      return false;
    }
    return true;
  }, [cancel]);

  // SO4: unmount/navigation cleanup — never leave audio running.
  useEffect(() => () => {
    const active = activeRef.current;
    if (active) active.cancelled = true;
    activeRef.current = null;
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    }
  }, []);

  return { available, status, lastOutcome, speak, cancel };
}
