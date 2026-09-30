/**
 * ZORQ Phase 3F — voice state machine contract tests (3F.1).
 *
 * Pure-node tests of frontend/lib/voiceMachine.ts against the legal/illegal
 * transition table of ZORQ-PHASE3F-VOICE-SPECIFICATION-v1.md §7.
 *
 * Run: `npm run test:voice` (compiles with tsc, executes with node).
 * Exit code 0 = all assertions passed; any failure exits 1 with detail.
 */
import {
  ALL_VOICE_EVENTS,
  ALL_VOICE_STATES,
  isBareSpeechStopUtterance,
  isLegal,
  SPEECH_STOP_CONTROL_UTTERANCES,
  telemetryToken,
  transition,
  type VoiceEvent,
  type VoiceState,
} from "../lib/voiceMachine";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function check(name: string, cond: boolean): void {
  if (cond) { passed += 1; }
  else { failed += 1; failures.push(name); }
}

function expectLegal(from: VoiceState, event: VoiceEvent, to: VoiceState): void {
  const r = transition(from, event);
  check(`${from} + ${event} -> ${to}`, r.ok && r.next === to);
}

function expectIllegal(from: VoiceState, event: VoiceEvent): void {
  const r = transition(from, event);
  check(`ILLEGAL ${from} + ${event}`, !r.ok);
}

// ---------------------------------------------------------------- happy path
expectLegal("IDLE", "MIC_REQUESTED", "REQUESTING_PERMISSION");
expectLegal("REQUESTING_PERMISSION", "RECOGNITION_ACTIVE", "LISTENING");
expectLegal("LISTENING", "USER_STOPPED_LISTENING", "TRANSCRIBING");
expectLegal("TRANSCRIBING", "TRANSCRIPT_FINALIZED", "IDLE");
expectLegal("IDLE", "VOICE_TURN_SUBMITTED", "PROCESSING");
expectLegal("PROCESSING", "SPEECH_STARTED", "SPEAKING");
expectLegal("SPEAKING", "SPEECH_COMPLETED", "IDLE");

// ------------------------------------------------------------ barge-in flow
expectLegal("SPEAKING", "STOP_SPEECH_REQUESTED", "INTERRUPTING");
expectLegal("INTERRUPTING", "SPEECH_CANCELLED", "IDLE");
// Continuation to mic happens ONLY on real recognition-start evidence:
expectLegal("INTERRUPTING", "RECOGNITION_ACTIVE", "LISTENING");
// Cancelled audio is never auto-resumed:
expectIllegal("INTERRUPTING", "SPEECH_STARTED");
expectIllegal("INTERRUPTING", "SPEECH_COMPLETED");

// --------------------------------------------------------------- empty/cancel
expectLegal("LISTENING", "RECOGNITION_EMPTY", "IDLE");
expectLegal("LISTENING", "TRANSCRIPT_FINALIZED", "IDLE"); // auto-end w/ final
expectLegal("TRANSCRIBING", "RECOGNITION_EMPTY", "IDLE");
expectLegal("LISTENING", "USER_CANCELLED", "IDLE");
expectLegal("TRANSCRIBING", "USER_CANCELLED", "IDLE");
expectLegal("REQUESTING_PERMISSION", "USER_CANCELLED", "IDLE");

// --------------------------------------------------------------------- errors
expectLegal("REQUESTING_PERMISSION", "PERMISSION_DENIED", "ERROR");
expectLegal("LISTENING", "RECOGNITION_ERROR", "ERROR");
expectLegal("SPEAKING", "SPEECH_ERROR", "ERROR");
expectLegal("ERROR", "ERROR_ACKNOWLEDGED", "IDLE");
expectLegal("ERROR", "MIC_REQUESTED", "REQUESTING_PERMISSION");
// Pre-start utterance failure is silent degradation, not a fault state:
expectLegal("PROCESSING", "SPEECH_ERROR", "IDLE");
expectLegal("PROCESSING", "TURN_COMPLETED_SILENT", "IDLE");
expectLegal("PROCESSING", "TURN_FAILED", "IDLE");

// -------------------------------------------------- spec §7 illegal examples
// IDLE cannot jump into transcription or interruption:
expectIllegal("IDLE", "USER_STOPPED_LISTENING");
expectIllegal("IDLE", "STOP_SPEECH_REQUESTED");
expectIllegal("IDLE", "TRANSCRIPT_FINALIZED");
// REQUESTING_PERMISSION cannot claim results or speech:
expectIllegal("REQUESTING_PERMISSION", "TRANSCRIPT_FINALIZED");
expectIllegal("REQUESTING_PERMISSION", "SPEECH_STARTED");
// LISTENING and SPEAKING are mutually exclusive in 3F-min:
expectIllegal("LISTENING", "SPEECH_STARTED");
expectIllegal("SPEAKING", "RECOGNITION_ACTIVE");
expectIllegal("SPEAKING", "MIC_REQUESTED");
// SPEAKING cannot silently return to LISTENING without INTERRUPTING/IDLE:
expectIllegal("SPEAKING", "USER_STOPPED_LISTENING");
// PROCESSING keeps the mic closed (3F-min):
expectIllegal("PROCESSING", "MIC_REQUESTED");
expectIllegal("PROCESSING", "RECOGNITION_ACTIVE");
// UNAVAILABLE is terminal for the surface:
for (const ev of ALL_VOICE_EVENTS) expectIllegal("UNAVAILABLE", ev);
// ERROR cannot resume transports without acknowledgement/new attempt:
expectIllegal("ERROR", "SPEECH_STARTED");
expectIllegal("ERROR", "RECOGNITION_ACTIVE");
expectIllegal("ERROR", "VOICE_TURN_SUBMITTED");

// ----------------------------------------------- stale-completion protection
// A terminal speech event has no meaning outside SPEAKING/PROCESSING: a stale
// onend from a superseded utterance must be rejected by the table itself.
for (const s of ["IDLE", "LISTENING", "TRANSCRIBING", "REQUESTING_PERMISSION", "ERROR"] as VoiceState[]) {
  expectIllegal(s, "SPEECH_COMPLETED");
}

// ------------------------------------------------------------ visibility loss
for (const s of ["REQUESTING_PERMISSION", "LISTENING", "TRANSCRIBING", "PROCESSING", "SPEAKING", "INTERRUPTING"] as VoiceState[]) {
  expectLegal(s, "VISIBILITY_LOST", "IDLE");
}
expectIllegal("IDLE", "VISIBILITY_LOST");

// --------------------------------------------------------------- capability
expectLegal("IDLE", "CAPABILITY_ABSENT", "UNAVAILABLE");
expectLegal("ERROR", "CAPABILITY_ABSENT", "UNAVAILABLE");

// ------------------------------------------------------- no STOPPED state
check("no STOPPED state exists", !(ALL_VOICE_STATES as readonly string[]).includes("STOPPED"));

// -------------------------------------------------- telemetry truthfulness
check("UNAVAILABLE token", telemetryToken("UNAVAILABLE", false) === "UNAVAILABLE");
check("IDLE+supported token", telemetryToken("IDLE", true) === "AVAILABLE");
check("IDLE+unsupported token", telemetryToken("IDLE", false) === "UNAVAILABLE");
check("LISTENING token", telemetryToken("LISTENING", true) === "LISTENING");
check("TRANSCRIBING token", telemetryToken("TRANSCRIBING", true) === "TRANSCRIBING");
check("PROCESSING token", telemetryToken("PROCESSING", true) === "PROCESSING");
check("SPEAKING token", telemetryToken("SPEAKING", true) === "SPEAKING");
check("INTERRUPTING token", telemetryToken("INTERRUPTING", true) === "INTERRUPTED");

// ------------------------------------------------- exhaustive table sanity
// Every legal transition must land on a declared state.
for (const s of ALL_VOICE_STATES) {
  for (const ev of ALL_VOICE_EVENTS) {
    if (isLegal(s, ev)) {
      const r = transition(s, ev);
      check(`target of ${s}+${ev} is a declared state`,
        r.ok && (ALL_VOICE_STATES as readonly string[]).includes(r.next));
    }
  }
}

// -------------------------------------------- spec §10 B6 control classifier
// Bare "stop" (barge-in context only — context is enforced by the caller in
// useVoice.ts; the classifier itself must be exact about bareness).
check("B6: control set is minimal and contains 'stop'",
  SPEECH_STOP_CONTROL_UTTERANCES.includes("stop") &&
  SPEECH_STOP_CONTROL_UTTERANCES.length <= 2);
// Utterances that ARE bare speech-stop controls:
for (const t of ["stop", "Stop", "STOP", " stop ", "stop.", "Stop!", "stop, stop",
                 "stop stop", "Stop."]) {
  check(`B6 control: ${JSON.stringify(t)}`, isBareSpeechStopUtterance(t) === true);
}
// Utterances that are ORDINARY INPUT (never consumed as a control):
for (const t of ["please stop", "stop the deployment", "stop it", "don't stop",
                 "stopwatch", "nonstop", "stop and delete my notes",
                 "can you stop", "", "   ", "yes", "stop everything"]) {
  check(`B6 ordinary input: ${JSON.stringify(t)}`, isBareSpeechStopUtterance(t) === false);
}
// B6 never escalates: the classifier is a pure predicate — it exposes no
// transport, no machine event, and no state. Guard the module surface so a
// future edit cannot silently turn it into a control channel.
check("B6: classifier returns a boolean only",
  typeof isBareSpeechStopUtterance("stop") === "boolean");

// --------------------------------------------------------------------- report
if (failed > 0) {
  console.error(`VOICE MACHINE TESTS: ${failed} FAILED, ${passed} passed`);
  for (const f of failures) console.error(`  FAIL: ${f}`);
  process.exit(1);
}
console.log(`VOICE MACHINE TESTS: ${passed} passed, 0 failed`);
