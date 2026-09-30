/**
 * ZORQ Phase 3F voice state machine (3F-min).
 *
 * Pure, dependency-free transition authority for the browser voice transport.
 * Every state change in the voice layer MUST go through `transition()`; the
 * controller hook (`useVoice`) owns the async transports but never mutates
 * state outside this table. Illegal transitions are rejected (not silently
 * coerced), which is what keeps UI voice state truthful.
 *
 * Contract: docs/zorq/ZORQ-PHASE3F-VOICE-SPECIFICATION-v1.md §7.
 * Invariant: FRONTEND STATE != AUTHORITATIVE STATE — this machine describes
 * the voice *transport* only. It carries no conversation, generation, memory,
 * or action authority of any kind.
 */

export type VoiceState =
  | "UNAVAILABLE"
  | "IDLE"
  | "REQUESTING_PERMISSION"
  | "LISTENING"
  | "TRANSCRIBING"
  | "PROCESSING"
  | "SPEAKING"
  | "INTERRUPTING"
  | "ERROR";

export type VoiceEvent =
  /** No recognition constructor and no usable capability at mount. */
  | "CAPABILITY_ABSENT"
  /** User pressed the mic while idle. */
  | "MIC_REQUESTED"
  /** Recognition actually started (real onstart/onaudiostart evidence). */
  | "RECOGNITION_ACTIVE"
  /** Browser reported not-allowed / permission failure. */
  | "PERMISSION_DENIED"
  /** User stopped listening; final results may still be pending. */
  | "USER_STOPPED_LISTENING"
  /** Recognition finished and a non-empty final transcript is the draft. */
  | "TRANSCRIPT_FINALIZED"
  /** Recognition finished with nothing usable. */
  | "RECOGNITION_EMPTY"
  /** Recognition transport error (device lost, network, aborted by UA...). */
  | "RECOGNITION_ERROR"
  /** User cancelled the voice attempt; draft is discarded by the controller. */
  | "USER_CANCELLED"
  /** A voice-originated draft was submitted as an ordinary user message. */
  | "VOICE_TURN_SUBMITTED"
  /** The submitted turn reached a terminal outcome with no speech output. */
  | "TURN_COMPLETED_SILENT"
  /** The submitted turn failed (provider/HTTP error). Text path shows it. */
  | "TURN_FAILED"
  /** Speech synthesis utterance actually started (real onstart evidence). */
  | "SPEECH_STARTED"
  /** Utterance finished naturally (real onend evidence, not cancelled). */
  | "SPEECH_COMPLETED"
  /** Utterance failed after starting. */
  | "SPEECH_ERROR"
  /** User (stop control or barge-in) requested speech-output stop. */
  | "STOP_SPEECH_REQUESTED"
  /** Synthesis cancel confirmed; no microphone continuation requested. */
  | "SPEECH_CANCELLED"
  /** ERROR acknowledged by the user (or superseded by a new attempt). */
  | "ERROR_ACKNOWLEDGED"
  /** Tab/visibility loss: active voice transports resolve truthfully. */
  | "VISIBILITY_LOST";

/**
 * The single legal-transition table (spec §7). Anything absent is illegal.
 *
 * Notes on deliberate entries:
 * - INTERRUPTING + RECOGNITION_ACTIVE → LISTENING is the barge-in
 *   continuation path: the mic transitions to LISTENING only on real
 *   recognition-start evidence, never optimistically.
 * - PROCESSING + SPEECH_ERROR → IDLE (not ERROR): an utterance that fails
 *   before producing any audio is a silent degradation — the response text
 *   is already rendered, nothing was lost (spec §20).
 * - There is intentionally no STOPPED state and no transition that lets a
 *   terminal speech event leave INTERRUPTING in place (spec §7 rationale).
 */
const TRANSITIONS: Readonly<Record<VoiceState, Partial<Record<VoiceEvent, VoiceState>>>> = {
  UNAVAILABLE: {
    // Terminal for the session surface; capability is re-evaluated per load.
  },
  IDLE: {
    CAPABILITY_ABSENT: "UNAVAILABLE",
    MIC_REQUESTED: "REQUESTING_PERMISSION",
    VOICE_TURN_SUBMITTED: "PROCESSING",
    // A voice turn submitted in a previous interaction may speak after the
    // machine already returned to IDLE (e.g. rapid stop→result ordering).
    SPEECH_STARTED: "SPEAKING",
  },
  REQUESTING_PERMISSION: {
    RECOGNITION_ACTIVE: "LISTENING",
    PERMISSION_DENIED: "ERROR",
    RECOGNITION_ERROR: "ERROR",
    USER_CANCELLED: "IDLE",
    VISIBILITY_LOST: "IDLE",
  },
  LISTENING: {
    USER_STOPPED_LISTENING: "TRANSCRIBING",
    TRANSCRIPT_FINALIZED: "IDLE",
    RECOGNITION_EMPTY: "IDLE",
    RECOGNITION_ERROR: "ERROR",
    PERMISSION_DENIED: "ERROR",
    USER_CANCELLED: "IDLE",
    VISIBILITY_LOST: "IDLE",
  },
  TRANSCRIBING: {
    TRANSCRIPT_FINALIZED: "IDLE",
    RECOGNITION_EMPTY: "IDLE",
    RECOGNITION_ERROR: "ERROR",
    USER_CANCELLED: "IDLE",
    VISIBILITY_LOST: "IDLE",
  },
  PROCESSING: {
    TURN_COMPLETED_SILENT: "IDLE",
    TURN_FAILED: "IDLE",
    SPEECH_STARTED: "SPEAKING",
    SPEECH_ERROR: "IDLE",
    VISIBILITY_LOST: "IDLE",
  },
  SPEAKING: {
    SPEECH_COMPLETED: "IDLE",
    STOP_SPEECH_REQUESTED: "INTERRUPTING",
    SPEECH_ERROR: "ERROR",
    VISIBILITY_LOST: "IDLE",
  },
  INTERRUPTING: {
    SPEECH_CANCELLED: "IDLE",
    RECOGNITION_ACTIVE: "LISTENING",
    PERMISSION_DENIED: "ERROR",
    RECOGNITION_ERROR: "ERROR",
    VISIBILITY_LOST: "IDLE",
  },
  ERROR: {
    ERROR_ACKNOWLEDGED: "IDLE",
    MIC_REQUESTED: "REQUESTING_PERMISSION", // a fresh attempt acknowledges
    CAPABILITY_ABSENT: "UNAVAILABLE",
  },
};

export interface TransitionOk {
  ok: true;
  next: VoiceState;
}
export interface TransitionRejected {
  ok: false;
  reason: string;
}
export type TransitionResult = TransitionOk | TransitionRejected;

/** The single transition authority. */
export function transition(state: VoiceState, event: VoiceEvent): TransitionResult {
  const next = TRANSITIONS[state]?.[event];
  if (next === undefined) {
    return { ok: false, reason: `illegal transition: ${state} + ${event}` };
  }
  return { ok: true, next };
}

export function isLegal(state: VoiceState, event: VoiceEvent): boolean {
  return TRANSITIONS[state]?.[event] !== undefined;
}

export const ALL_VOICE_STATES: readonly VoiceState[] = [
  "UNAVAILABLE", "IDLE", "REQUESTING_PERMISSION", "LISTENING", "TRANSCRIBING",
  "PROCESSING", "SPEAKING", "INTERRUPTING", "ERROR",
];

export const ALL_VOICE_EVENTS: readonly VoiceEvent[] = [
  "CAPABILITY_ABSENT", "MIC_REQUESTED", "RECOGNITION_ACTIVE", "PERMISSION_DENIED",
  "USER_STOPPED_LISTENING", "TRANSCRIPT_FINALIZED", "RECOGNITION_EMPTY",
  "RECOGNITION_ERROR", "USER_CANCELLED", "VOICE_TURN_SUBMITTED",
  "TURN_COMPLETED_SILENT", "TURN_FAILED", "SPEECH_STARTED", "SPEECH_COMPLETED",
  "SPEECH_ERROR", "STOP_SPEECH_REQUESTED", "SPEECH_CANCELLED",
  "ERROR_ACKNOWLEDGED", "VISIBILITY_LOST",
];

/**
 * Truthful telemetry token for a machine state (spec §17). Only states backed
 * by real runtime evidence reach this function, because the machine itself
 * only moves on real events.
 */
export function telemetryToken(state: VoiceState, supported: boolean): string {
  switch (state) {
    case "UNAVAILABLE": return "UNAVAILABLE";
    case "IDLE": return supported ? "AVAILABLE" : "UNAVAILABLE";
    // Transient permission wait: the underlying capability truth is shown
    // (spec §7: telemetry "none (transient)" — capability remains AVAILABLE).
    case "REQUESTING_PERMISSION": return "AVAILABLE";
    case "LISTENING": return "LISTENING";
    case "TRANSCRIBING": return "TRANSCRIBING";
    case "PROCESSING": return "PROCESSING";
    case "SPEAKING": return "SPEAKING";
    case "INTERRUPTING": return "INTERRUPTED";
    case "ERROR": return "ERROR";
  }
}

/* ------------------------------------------------------------------------ *
 * Spec §10 B6 — bare "stop" during barge-in.
 *
 * CONTEXTUAL BOUNDARY (normative for this implementation):
 * The classifier below applies ONLY to the final transcript of a recognition
 * session that was opened as a barge-in continuation (SPEAKING →
 * STOP_SPEECH_REQUESTED → INTERRUPTING → LISTENING). In that context — and
 * in no other — a bare control utterance is the speech-output stop it
 * contextually is: STOP(target=SPEECH), already satisfied by the barge-in
 * itself. The utterance is therefore consumed as a control acknowledgement:
 * it is NOT placed in the draft and is NEVER submitted to /api/chat.
 *
 * Everywhere else — a mic session started from IDLE/ERROR, typed text, or
 * any barge-in utterance that is not a bare control word — "stop" is
 * ordinary user input: it becomes a normal editable draft / message.
 *
 * This is NOT a second interruption architecture: it adds no state, no
 * event, no epoch, no producer identity, and no generation control. It can
 * never escalate to STOP(target=ACTION), STOP(target=RESPONSE_GENERATION),
 * PAUSE, or CANCEL, because no code path from here reaches any transport —
 * the classifier's only effect is that a draft is not created.
 * ------------------------------------------------------------------------ */

/**
 * The exact bare control utterances recognized in the barge-in context.
 * Deliberately minimal (spec B6 names "stop"); anything longer or different
 * is ordinary input.
 */
export const SPEECH_STOP_CONTROL_UTTERANCES: readonly string[] = [
  "stop", "stop stop",
];

/**
 * True iff `text`, normalized (lowercase, surrounding whitespace and
 * terminal punctuation removed, inner whitespace collapsed), is exactly a
 * bare speech-stop control utterance. Pure and context-free: the CALLER is
 * responsible for applying it only in the barge-in context (see boundary
 * note above).
 */
export function isBareSpeechStopUtterance(text: string): boolean {
  const normalized = text
    .toLowerCase()
    .replace(/[.,!?;:]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return SPEECH_STOP_CONTROL_UTTERANCES.includes(normalized);
}
