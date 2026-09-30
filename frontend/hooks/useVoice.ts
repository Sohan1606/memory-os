"use client";
/**
 * ZORQ Phase 3F voice controller (3F-min).
 *
 * Owns the voice transport lifecycle: browser SpeechRecognition input,
 * tracked speech-synthesis output (useSpeechOutput), and the pure state
 * machine in lib/voiceMachine.ts. All state changes flow through the
 * machine's transition table; illegal or stale events are dropped and
 * counted, never applied.
 *
 * Truthfulness/authority rules (spec §§6–10, 26):
 *  - The transcript is a DRAFT until the user submits it through the same
 *    submission path as typed text. Interim results are never submittable.
 *  - Voice state carries no authority: FRONTEND STATE != AUTHORITATIVE STATE.
 *  - Barge-in (3F-min) stops SPEECH OUTPUT only — STOP(target=SPEECH). It
 *    never cancels or pauses response generation, and never escalates to
 *    STOP(target=RESPONSE_GENERATION) or any Action Plane control.
 *  - Every async recognition/synthesis callback is guarded by a session
 *    generation: an event from a superseded voice session cannot mutate
 *    newer state.
 *
 * Privacy (spec §15, GAP-1): browser speech recognition may transmit audio
 * to the browser vendor's speech service — this is a browser property ZORQ
 * does not control. The UI copy discloses it; this module never claims
 * local-only recognition.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  isBareSpeechStopUtterance,
  telemetryToken,
  transition,
  type VoiceEvent,
  type VoiceState,
} from "@/lib/voiceMachine";
import { useSpeechOutput, type SpeechOutput } from "@/hooks/useSpeechOutput";

/** Recognition language (GAP-8: explicit and labeled, not silently assumed). */
export const VOICE_INPUT_LANGUAGE = "en-US";

/** GAP-1 disclosure — single source for all voice-input UI copy. */
export const BROWSER_STT_DISCLOSURE =
  "Browser speech recognition; your browser may send audio to its vendor's " +
  "speech service. ZORQ itself never stores or transmits raw audio.";

interface RecognitionAlternative { transcript: string }
interface RecognitionResult extends ArrayLike<RecognitionAlternative> { isFinal: boolean }
interface RecognitionResultEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onaudiostart: (() => void) | null;
  onstart: (() => void) | null;
  onresult: ((e: RecognitionResultEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => SpeechRecognitionLike;

function recognitionConstructor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

interface RecognitionSession {
  generation: number;
  recognition: SpeechRecognitionLike;
  /** true when we initiated stop() (finalization still expected). */
  stopping: boolean;
  /** true when we initiated abort() (all events after are ours to ignore). */
  aborted: boolean;
  finalText: string;
  sawActive: boolean;
  /**
   * true iff this session is a barge-in continuation (INTERRUPTING →
   * LISTENING). Spec §10 B6: ONLY in this context a bare "stop" utterance
   * is consumed as the STOP(target=SPEECH) it contextually is, instead of
   * becoming a draft. Sessions started from IDLE/ERROR never set this.
   */
  bargeIn: boolean;
}

export interface VoiceControl {
  /** Machine state — transport truth only, never authority. */
  state: VoiceState;
  /** Truthful telemetry token for the Z-UI telemetry layer (spec §17). */
  telemetry: string;
  /** Whether browser speech recognition exists here. */
  supported: boolean;
  /** Whether browser speech synthesis exists here. */
  outputAvailable: boolean;
  /** Provisional recognition text — visibly draft, never submittable. */
  interimTranscript: string;
  /** Finalized recognition text — an editable DRAFT until submitted. */
  finalTranscript: string;
  /** Truthful failure reason while in ERROR. */
  error: string | null;
  /** Stale/illegal events dropped by the guards (QA/observability). */
  droppedEvents: number;
  /**
   * Count of bare "stop" barge-in utterances consumed as STOP(target=SPEECH)
   * per spec §10 B6 — each one produced NO draft and NO /api/chat submission.
   */
  speechStopControls: number;
  language: string;
  sttDisclosure: string;

  startListening: () => void;
  /** Stop and keep whatever finalizes as the draft. */
  stopListening: () => void;
  /** Abort and discard everything from this voice session. */
  cancelListening: () => void;
  /** Clear the draft after the caller has consumed it. */
  clearDraft: () => void;
  /** The caller submitted a voice-originated draft as a normal message. */
  notifySubmitted: () => void;
  /** The submitted turn ended and no speech output will follow. */
  notifyTurnDone: (ok: boolean) => void;
  /** Speak the exact rendered response text (optional output; SO1). */
  speak: (text: string) => void;
  /** Stop speech output only — STOP(target=SPEECH). Never touches generation. */
  stopSpeaking: () => void;
  /** Barge-in: stop speech output, then open the microphone (3F-min §10). */
  bargeIn: () => void;
  acknowledgeError: () => void;
}

export function useVoice(): VoiceControl {
  const [state, setState] = useState<VoiceState>("IDLE");
  const [supported, setSupported] = useState(false);
  const [interimTranscript, setInterim] = useState("");
  const [finalTranscript, setFinal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [droppedEvents, setDropped] = useState(0);
  const [speechStopControls, setSpeechStopControls] = useState(0);

  const stateRef = useRef<VoiceState>("IDLE");
  const generationRef = useRef(0);
  const sessionRef = useRef<RecognitionSession | null>(null);
  const continueToMicRef = useRef(false);
  const supportedRef = useRef(false);
  const output: SpeechOutput = useSpeechOutput();

  /** Single dispatch authority — machine table decides, nothing else. */
  const dispatch = useCallback((event: VoiceEvent): boolean => {
    const result = transition(stateRef.current, event);
    if (!result.ok) {
      setDropped((n) => n + 1);
      return false;
    }
    stateRef.current = result.next;
    setState(result.next);
    return true;
  }, []);

  // Capability detection (truthful, once per mount).
  useEffect(() => {
    const ctor = recognitionConstructor();
    supportedRef.current = ctor !== null;
    setSupported(ctor !== null);
    if (ctor === null) dispatch("CAPABILITY_ABSENT");
  }, [dispatch]);

  const teardownSession = useCallback((abort: boolean) => {
    const session = sessionRef.current;
    if (!session) return;
    session.aborted = session.aborted || abort;
    const rec = session.recognition;
    rec.onaudiostart = null; rec.onstart = null; rec.onresult = null;
    rec.onerror = null; rec.onend = null;
    if (abort) { try { rec.abort(); } catch { /* already stopped */ } }
    sessionRef.current = null;
  }, []);

  /** Start a fresh, generation-guarded recognition session. */
  const beginRecognition = useCallback((origin: "user" | "barge-in" = "user"): boolean => {
    const ctor = recognitionConstructor();
    if (!ctor) return false;
    teardownSession(true);

    const generation = ++generationRef.current;
    const rec = new ctor();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = VOICE_INPUT_LANGUAGE;

    const session: RecognitionSession = {
      generation, recognition: rec, stopping: false, aborted: false,
      finalText: "", sawActive: false, bargeIn: origin === "barge-in",
    };
    // Stale guard: every handler checks it belongs to the live session.
    const isCurrent = () =>
      sessionRef.current === session && session.generation === generationRef.current;
    const stale = () => { setDropped((n) => n + 1); };

    const markActive = () => {
      if (!isCurrent()) return stale();
      if (session.sawActive || session.aborted) return;
      session.sawActive = true;
      dispatch("RECOGNITION_ACTIVE"); // REQUESTING_PERMISSION|INTERRUPTING → LISTENING
    };
    rec.onaudiostart = markActive;
    rec.onstart = markActive;

    rec.onresult = (e) => {
      if (!isCurrent()) return stale();
      if (session.aborted) return;
      let finalText = "";
      let interimText = "";
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        const alt = r[0]?.transcript ?? "";
        if (r.isFinal) finalText += alt;
        else interimText += alt;
      }
      session.finalText = finalText.trim();
      setInterim(interimText.trim());
      // Draft (final) text is only surfaced once finalized below; interim is
      // provisional display only and never submittable (TR3).
    };

    rec.onerror = (e) => {
      if (!isCurrent()) return stale();
      if (session.aborted) return; // our own abort — USER_CANCELLED already ran
      teardownSession(false);
      setInterim("");
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setError("Microphone permission denied. Text mode is fully supported.");
        dispatch("PERMISSION_DENIED");
      } else if (e.error === "no-speech") {
        dispatch("RECOGNITION_EMPTY");
      } else {
        setError(`Speech recognition error: ${e.error}. Text mode is fully supported.`);
        dispatch("RECOGNITION_ERROR");
      }
    };

    rec.onend = () => {
      if (!isCurrent()) return stale();
      if (session.aborted) return;
      teardownSession(false);
      setInterim("");
      const text = session.finalText;
      if (text && session.bargeIn && isBareSpeechStopUtterance(text)) {
        // Spec §10 B6: in the barge-in context ONLY, a bare "stop" is the
        // speech-output stop it contextually is — STOP(target=SPEECH),
        // already satisfied by the barge-in that cancelled synthesis. It is
        // consumed here: NO draft, NO /api/chat submission, no authority-
        // plane record, and no escalation of any kind (this branch reaches
        // no transport; output.cancel() is a defensive local no-op if idle).
        output.cancel();
        setSpeechStopControls((n) => n + 1);
        dispatch("RECOGNITION_EMPTY"); // no draft produced → IDLE
      } else if (text) {
        setFinal(text);
        dispatch("TRANSCRIPT_FINALIZED"); // LISTENING|TRANSCRIBING → IDLE (draft)
      } else {
        dispatch("RECOGNITION_EMPTY");
      }
    };

    sessionRef.current = session;
    try {
      rec.start();
    } catch {
      teardownSession(true);
      setError("Could not start listening. Text mode is fully supported.");
      // From REQUESTING_PERMISSION or INTERRUPTING this is a transport error.
      dispatch("RECOGNITION_ERROR");
      return false;
    }
    return true;
  }, [dispatch, output, teardownSession]);

  const startListening = useCallback(() => {
    if (!supportedRef.current) return;
    const s = stateRef.current;
    if (s !== "IDLE" && s !== "ERROR") return; // MIC_REQUESTED illegal elsewhere
    setError(null);
    setFinal("");
    setInterim("");
    if (!dispatch("MIC_REQUESTED")) return; // IDLE|ERROR → REQUESTING_PERMISSION
    beginRecognition();
  }, [beginRecognition, dispatch]);

  const stopListening = useCallback(() => {
    const session = sessionRef.current;
    if (!session || stateRef.current !== "LISTENING") return;
    session.stopping = true;
    dispatch("USER_STOPPED_LISTENING"); // LISTENING → TRANSCRIBING
    try { session.recognition.stop(); } catch { /* already ended */ }
    // Finalization arrives via onend → TRANSCRIPT_FINALIZED | RECOGNITION_EMPTY.
  }, [dispatch]);

  const cancelListening = useCallback(() => {
    const s = stateRef.current;
    if (s !== "REQUESTING_PERMISSION" && s !== "LISTENING" && s !== "TRANSCRIBING") return;
    teardownSession(true);
    setInterim("");
    setFinal("");
    dispatch("USER_CANCELLED");
  }, [dispatch, teardownSession]);

  const clearDraft = useCallback(() => { setFinal(""); }, []);

  const notifySubmitted = useCallback(() => {
    dispatch("VOICE_TURN_SUBMITTED"); // IDLE → PROCESSING (telemetry mirror)
  }, [dispatch]);

  const notifyTurnDone = useCallback((_ok: boolean) => {
    if (stateRef.current === "PROCESSING") {
      dispatch(_ok ? "TURN_COMPLETED_SILENT" : "TURN_FAILED");
    }
  }, [dispatch]);

  const speak = useCallback((text: string) => {
    if (!output.available) {
      // SO1: optional output — silent, lossless degradation.
      if (stateRef.current === "PROCESSING") dispatch("TURN_COMPLETED_SILENT");
      return;
    }
    const attempted = output.speak(text, {
      onStart: () => { dispatch("SPEECH_STARTED"); },
      onComplete: () => { dispatch("SPEECH_COMPLETED"); },
      onCancelled: () => {
        if (continueToMicRef.current) {
          // Barge-in continuation: stay in INTERRUPTING until the mic gives
          // real recognition-start evidence (INTERRUPTING → LISTENING).
          continueToMicRef.current = false;
          setError(null);
          setFinal("");
          setInterim("");
          if (!beginRecognition("barge-in")) dispatch("SPEECH_CANCELLED");
        } else {
          dispatch("SPEECH_CANCELLED"); // INTERRUPTING → IDLE
        }
      },
      onError: () => { dispatch("SPEECH_ERROR"); },
    });
    if (!attempted && stateRef.current === "PROCESSING") {
      dispatch("TURN_COMPLETED_SILENT");
    }
  }, [beginRecognition, dispatch, output]);

  const stopSpeaking = useCallback(() => {
    if (stateRef.current !== "SPEAKING") return;
    continueToMicRef.current = false;
    // High-priority local path: no network, no model cycle (spec §10 B2).
    dispatch("STOP_SPEECH_REQUESTED"); // SPEAKING → INTERRUPTING
    output.cancel();                    // → onCancelled → SPEECH_CANCELLED → IDLE
  }, [dispatch, output]);

  const bargeIn = useCallback(() => {
    if (stateRef.current !== "SPEAKING") return;
    // STOP(target=SPEECH) only — generation is NEVER touched (3F-min).
    continueToMicRef.current = supportedRef.current;
    dispatch("STOP_SPEECH_REQUESTED");
    output.cancel(); // → onCancelled → mic continuation or IDLE
  }, [dispatch, output]);

  const acknowledgeError = useCallback(() => {
    if (stateRef.current !== "ERROR") return;
    setError(null);
    dispatch("ERROR_ACKNOWLEDGED");
  }, [dispatch]);

  // Tab backgrounding: resolve active transports truthfully (spec §19/§20).
  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) return;
      const s = stateRef.current;
      if (s === "REQUESTING_PERMISSION" || s === "LISTENING" || s === "TRANSCRIBING"
          || s === "SPEAKING" || s === "INTERRUPTING") {
        continueToMicRef.current = false;
        dispatch("VISIBILITY_LOST"); // → IDLE first, then transports cleaned
        teardownSession(true);
        output.cancel();
        setInterim("");
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [dispatch, output, teardownSession]);

  // Unmount: never leave a live microphone or utterance behind.
  useEffect(() => () => { teardownSession(true); }, [teardownSession]);

  return {
    state,
    telemetry: telemetryToken(state, supported),
    supported,
    outputAvailable: output.available,
    interimTranscript,
    finalTranscript,
    error,
    droppedEvents,
    speechStopControls,
    language: VOICE_INPUT_LANGUAGE,
    sttDisclosure: BROWSER_STT_DISCLOSURE,
    startListening,
    stopListening,
    cancelListening,
    clearDraft,
    notifySubmitted,
    notifyTurnDone,
    speak,
    stopSpeaking,
    bargeIn,
    acknowledgeError,
  };
}
