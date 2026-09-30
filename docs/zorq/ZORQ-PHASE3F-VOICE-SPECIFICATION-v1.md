# ZORQ Phase 3F Voice Specification v1

**Status label:** SPECIFIED — NOT IMPLEMENTED. This document is a design lock for Phase 3F. No runtime behavior was changed by producing it.
**Baseline commit:** `1756552` (`feat: complete ZORQ Z-UI.1 intelligence console`, branch `zroq/canonical-migration`).
**Audit method:** direct inspection of frontend, backend, `src/zroq` runtime source, existing contracts, and tests. Every capability below is labeled IMPLEMENTED, PARTIAL, NOT IMPLEMENTED, or UNKNOWN based on what the repository actually contains at the baseline commit. Nothing is claimed from documentation alone.

---

## 1. Purpose

Define the complete, verifiable contract for the ZORQ Phase 3F voice runtime:
speech input, speech output, barge-in, interruption integration, transcript
authority, memory integration, privacy lifecycle, telemetry, accessibility,
and failure/degradation behavior — such that voice becomes a first-class
interaction modality **without weakening any existing authority, memory,
or verification boundary**.

Voice in ZORQ is a *convenience input/output transport*. It is never an
identity factor, never an authorization channel, and never a second reasoning
pipeline.

## 2. Scope

In scope for Phase 3F (future implementation, not this document):

- a truthful voice state machine in the frontend interaction layer;
- speech input via browser `SpeechRecognition` where available, with an
  optional server-side local-Whisper path (already present, currently dormant);
- speech output via browser `speechSynthesis` with full lifecycle tracking
  (currently fire-and-forget);
- barge-in: user speech interrupting ZORQ speech output, integrated with the
  **existing** Phase 3C/3C.1 response-control protocol — not a new one;
- convergence of voice input into the same conversation pipeline as typed text;
- voice telemetry surfaced through the existing Z-UI.1 telemetry layer;
- accessibility, mobile/desktop, and failure-model hardening;
- acceptance tests and browser QA extensions.

In scope for **this document**: the audit of current behavior, the contracts,
the work-package breakdown, the test plan, and the invariants.

## 3. Non-goals

Phase 3F explicitly does **not** include:

- wake word / always-listening capture (forbidden by
  `ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md` §1: "no hidden always-on recording");
- speaker verification, voice biometrics, or any voice-derived identity
  assurance (VOICE CONVENIENCE != HIGH-ASSURANCE IDENTITY);
- voice-initiated Action Plane execution beyond expressing intent through the
  same governed conversation path as text;
- a custom TTS voice, voice cloning, or multilingual speech
  (`ZORQ-INTERACTION-MODEL-v1.md` §3/§4 remain DESIGNED/DEFERRED);
- an "offline voice" claim (see §16);
- echo cancellation / acoustic VAD tuning beyond what browser APIs provide;
- any modification to grants, capabilities, confirmation, ActionSnapshot,
  leases, execution, verification, or audit semantics.

## 4. Current implementation audit

Files inspected directly at commit `1756552`:

| Area | File | Finding |
|---|---|---|
| Speech input hook | `frontend/hooks/useSpeech.ts` (85 lines) | IMPLEMENTED (browser-only). Wraps `SpeechRecognition ?? webkitSpeechRecognition`; `continuous=false`, `interimResults=true`, `lang="en-US"`. Exposes `{supported, listening, transcript, error, start, stop, reset}`. `not-allowed` mapped to a truthful permission-denied message. Cleanup aborts recognition on unmount. |
| Chat console | `frontend/components/Chat.tsx` | PARTIAL voice integration. Mic button fills the text input from `speech.transcript`; submission sets `interaction_mode="voice"`; on response, if mode was voice and `speechSynthesis` exists: `speechSynthesis.cancel()` then `speak(new SpeechSynthesisUtterance(res.answer))` — **fire-and-forget**: no utterance `onend`/`onerror`, no speaking state, no stop control, no barge-in. UI honestly labels the mic as "browser SpeechRecognition fallback (the real voice runtime is Phase 3F)". Unsupported browsers show `MIC N/A`. |
| Voice demo | `frontend/components/VoiceDemo.tsx` | IMPLEMENTED (demo). Voice or typed text → `createMemory(content, undefined, "voice")` — the same memory-creation pathway as other surfaces. Stage chips are UI-local (`IDLE/LISTENING/PROCESSING/…`), not runtime telemetry. Waveform is procedural (decorative), not real audio amplitude. |
| Server transcription | `backend/app/voice/transcription.py` (58 lines) | PARTIAL/DORMANT. Optional local faster-whisper (CPU, int8). Honest availability: `mode` = `"whisper"` or `"browser"`. `transcribe()` writes audio to a `NamedTemporaryFile` and deletes it in `finally`. **No frontend caller exists** — the repo contains no `MediaRecorder`/`getUserMedia` usage. |
| Voice endpoints | `backend/app/main.py` (`/api/voice/status`, `/api/voice/transcribe`) | IMPLEMENTED (status) / DORMANT (transcribe: 25 MB limit, 503 when Whisper unconfigured, 500 with detail on failure). |
| Voice events | `backend/app/main.py` chat route; `backend/app/cognition/events.py` | IMPLEMENTED. `interaction_mode="voice"` emits `voice.session_started` / `voice.session_ended` cognitive events with `transport: browser_speech_recognition`. |
| Perception layer | `backend/app/cognition/perception.py` | IMPLEMENTED. Voice is a first-class modality; voice channel reports `ACTIVE`/`DEGRADED` from real transcriber state; `perceive_audio` refuses honestly when no transcriber. |
| Self model | `backend/app/cognition/self_model.py` | IMPLEMENTED. Reports `voice_server` capability truthfully; states "voice uses your browser" when Whisper is absent. |
| Canonical runtime | `src/zroq/conversation_runtime.py` (1748 lines) | IMPLEMENTED, text-first. Full 3C/3C.1 control protocol (see §11). Module docstring states it intentionally does not implement voice/STT/TTS. |
| Domain contracts | `src/zroq/domain_contracts.py` | IMPLEMENTED. `SpeechState` (IDLE/SPEAKING/INTERRUPTED/PAUSED/RESUMING/CANCELED/COMPLETED), `GenerationState`, `ResumePolicy`, `InteractionCommandType` (STOP requires non-UNKNOWN target), `InteractionTarget` incl. `SPEECH`, `ResponseCursor` with `last_spoken_boundary` (currently always `text-only:no-audio-boundary`), `PrivacyClass`, `RetentionMode`/`RetentionOverride`. |
| ZORQ facade | `backend/app/zorq_facade.py` | IMPLEMENTED, GET-only. Reports voice truthfully as `{"state": "NOT-IMPLEMENTED", "phase": "Phase 3F"}`. |
| QA | `tests/zui1_browser_qa.py` | IMPLEMENTED. Forbidden-text scan includes identity-protection strings; voice demo covered by route QA. |
| Interruption tests | `tests/test_phase3c_conversation_runtime.py`, `tests/test_phase3c1_concurrent_interrupt.py` | IMPLEMENTED. Real-thread concurrency tests of the stale-producer rejection. |

Cross-referenced contracts (read in full or in relevant part):
`ZORQ-BARGE-IN-INTERRUPTION-v1.md`, `ZORQ-CONCURRENT-INTERRUPTION-v1.md`,
`ZORQ-INTERRUPTION-IMPLEMENTATION-v1.md`, `ZORQ-CONVERSATIONAL-RUNTIME-v1.md`,
`ZORQ-CONVERSATIONAL-RUNTIME-IMPLEMENTATION-v1.md`,
`ZORQ-INTERACTION-CONTROL-CONTRACT-v1.md`, `ZORQ-INTERACTION-MODEL-v1.md`,
`ZORQ-RESPONSE-CONTROL-PROTOCOL-v1.md`,
`ZORQ-RESPONSE-CURSOR-IMPLEMENTATION-v1.md`,
`ZORQ-ACTION-KERNEL-IMPLEMENTATION.md`, `ZORQ-OUTCOME-VERIFICATION-v2.md`,
`ZORQ-THREAT-TEST-CATALOG.md`, `ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md`,
`ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md`, `ZORQ-MEMORY-CAPTURE-POLICY-v1.md`,
`ZORQ-ZUI1-VERIFICATION-REPORT.md`.

### 4.1 Step 3 answers — current voice behavior, precisely

1. **Voice input:** browser `SpeechRecognition` one-shot session fills a text
   field; the user still submits through the normal console. IMPLEMENTED.
2. **Browser SpeechRecognition used?** Yes (`SpeechRecognition` or
   `webkitSpeechRecognition`). IMPLEMENTED.
3. **Web Speech API used?** Yes — both halves: recognition (input) and
   `speechSynthesis` (output). IMPLEMENTED (minimal).
4. **Microphone permission:** delegated entirely to the browser via
   `recognition.start()`. Denial surfaces as `error === "not-allowed"` →
   truthful message "Microphone permission denied. Use text mode instead."
   There is no explicit permission-priming UI. PARTIAL.
5. **Unsupported browsers:** `supported=false`; Chat renders `MIC N/A`;
   VoiceDemo says "text mode is fully supported". IMPLEMENTED (honest).
6. **What the transcript represents:** the concatenated Web Speech results
   (interim + final are not distinguished by the hook — it concatenates all
   result alternatives at index 0). It is treated as **draft text input**,
   editable before submission. IMPLEMENTED.
7. **Transcript persistence:** only if submitted — then it persists exactly
   as a normal chat message / memory record (source tagged `voice` in
   VoiceDemo). Unsubmitted transcripts are discarded in component state.
   IMPLEMENTED (by inheritance from text path).
8. **Audio persistence:** app-level: none on the browser path (no audio is
   ever captured by application code). Whisper path: temp file deleted in
   `finally`. What the **browser vendor** does with audio during recognition
   is outside repository control — see §15 GAP-1.
9. **Audio leaving the device:** application code sends no audio anywhere
   (no caller of `/api/voice/transcribe` exists). Browser-native recognition
   may stream audio to the browser vendor's service (e.g. Chromium-based
   browsers) — the repository neither controls nor documents this. GAP.
10. **Speech output:** `speechSynthesis.speak()` of the full answer text,
    only when the submitting turn used voice mode. Fire-and-forget. PARTIAL.
11. **Speech synthesis availability:** feature-detected per call
    (`"speechSynthesis" in window`); silently skipped when absent; text is
    always rendered regardless. IMPLEMENTED (degrades correctly).
12. **Synthesis cancellation:** `speechSynthesis.cancel()` is invoked only
    as a pre-speak flush before a new utterance. There is **no user-facing
    stop-speaking control** and no cancellation on navigation/unmount.
    PARTIAL.
13. **During generation:** the UI shows busy state and live cognitive
    surface polling; the mic button is disabled (`disabled={busy}`). Voice
    has no role during generation. IMPLEMENTED (text semantics).
14. **User speaks while ZORQ is speaking:** nothing. Recognition is not
    active during synthesis (one-shot, already ended), so there is **no
    barge-in of any kind**. NOT IMPLEMENTED.
15. **Pause/stop/cancel:** frontend voice: `stop()` ends listening;
    no speech-output stop. Canonical runtime (`src/zroq`): full
    STOP/PAUSE/CANCEL/SKIP/RESUME/CONTINUE protocol — but only for text
    generation state, and it is not wired to the HTTP chat path (see §6).
16. **Voice/text convergence:** at the HTTP layer, yes — a voice-originated
    message goes through exactly `/api/chat` like typed text (only
    `interaction_mode` differs, which affects telemetry events and whether
    synthesis is attempted). IMPLEMENTED at that layer. See §6 for the
    deeper runtime split.
17. **Existing voice states:** contract level: `SpeechState` enum (7 states,
    persisted per response, currently mirroring generation lifecycle with
    `last_spoken_boundary = "text-only:no-audio-boundary"`). UI level:
    `useSpeech.listening` boolean + VoiceDemo's local stage chips. There is
    **no voice state machine** in the interaction layer. PARTIAL.
18. **Existing voice telemetry:** `voice.session_started`/`voice.session_ended`
    cognitive events; `/api/voice/status`; health `voice.mode`; perception
    channel `ACTIVE/DEGRADED`; facade `voice.state = NOT-IMPLEMENTED`.
    IMPLEMENTED (truthful, minimal).
19. **Known browser limitations:** Web Speech recognition is absent in
    Firefox by default and inconsistently available elsewhere; `en-US` is
    hard-coded; `continuous=false` means one phrase per activation; iOS
    Safari synthesis requires a user gesture. The repository handles absence
    honestly but documents none of the per-browser detail. PARTIAL/UNKNOWN.

## 5. Current voice architecture

```text
[Browser mic] → SpeechRecognition (browser-native, vendor STT)
      → useSpeech.transcript → Chat input field (editable draft)
      → POST /api/chat {message, interaction_mode:"voice"}
      → backend/app cognition loop + agent  (same as typed text)
      → ChatResponse.answer
      → (voice mode only) speechSynthesis.speak(answer)   [fire-and-forget]

[Dormant] POST /api/voice/transcribe → faster-whisper (local, CPU) — no caller.
[Canonical] src/zroq ConversationRuntime — full control protocol, text-first,
            not wired to the HTTP chat path.
```

Two truths matter for 3F planning:

- **T1.** Voice already converges with text at the HTTP boundary. There is no
  separate voice reasoning path to remove.
- **T2.** The interruption/control substrate that barge-in requires
  (`ResponseControlState`, control epochs, stale rejection) lives in
  `src/zroq/conversation_runtime.py` and is **not exposed on the HTTP chat
  path** that the UI actually uses. Browser speech-output cancellation and
  runtime generation control are therefore currently **disjoint layers**.

## 6. Text/voice convergence

Required architecture (confirmed feasible against the current repository):

```text
VOICE INPUT → TRANSCRIPT → SAME CONVERSATION RUNTIME → SAME CONTEXT
→ SAME MEMORY GOVERNANCE → SAME PROVIDER ROUTING → SAME RESPONSE RUNTIME
→ OPTIONAL VOICE OUTPUT
```

Convergence rules:

- **C1.** A finalized transcript is submitted as an ordinary user message on
  the existing chat path. No voice-specific reasoning, prompting, provider
  selection, or memory route may exist.
- **C2.** `interaction_mode="voice"` may only affect: (a) truthful telemetry
  events, (b) whether speech output is *offered*. It must never affect
  retrieval, governance, authorization, or provider behavior.
- **C3.** The transcript is a **draft until submitted**. The user can edit it
  exactly like typed text. Auto-submit on recognition end is permitted only
  as an explicit user-enabled option (default OFF) and must still traverse
  the identical submission function.
- **C4.** Voice output consumes the final response text — the same text the
  UI renders. No separate "spoken answer" is generated.
- **C5 (decision gate).** Generation-level barge-in (STOP/PAUSE of an
  in-flight response) requires the 3C/3C.1 control protocol to be reachable
  from the UI. The current `/api/chat` is a blocking request/response with no
  streaming and no control surface. Phase 3F therefore has two lawful
  targets, and the implementation phase must pick one explicitly:
  - **3F-min:** barge-in controls *speech output only* (browser-local,
    high-priority path per `ZORQ-BARGE-IN-INTERRUPTION-v1.md` §1); generation
    control remains out of scope because the transport cannot express it.
  - **3F-full:** the chat transport is upgraded to streaming with the
    existing out-of-band control API (`interrupt_response`, `pause_response`,
    …) exposed per `ZORQ-RESPONSE-CONTROL-PROTOCOL-v1.md`; barge-in then maps
    to `STOP(target=SPEECH)` + optional `STOP(target=RESPONSE_GENERATION)`.
  This specification defines contracts for both; 3F-min is the floor and
  never blocks a later 3F-full. Neither target creates a new interruption
  state machine (§11).

## 7. Voice state machine

Selected states (evaluated against the candidate list; two candidates were
rejected — rationale below):

```text
UNAVAILABLE ─(capability discovered)─x   [terminal for the session surface]
IDLE → REQUESTING_PERMISSION → LISTENING → TRANSCRIBING → PROCESSING
                                   │                          │
IDLE ←── ERROR ←──(any failure)    │                          ▼
  ▲                                │                      SPEAKING
  │                                │                          │
  └────────(complete/cancel)───────┴──────── INTERRUPTING ←───┘
```

Rejected candidates:

- **STOPPED** — redundant. Every stop path resolves into `IDLE` (ready
  again) or `ERROR` (fault). A distinct STOPPED state would duplicate IDLE
  and invite drift between UI state and truth.
- A separate **generation state machine** — forbidden. Generation lifecycle
  already exists (`GenerationState`, `ResponseControlState`). `PROCESSING`
  below is a *view* of that existing lifecycle, not a second authority.

Per-state contract:

| State | Entry | Exit | Legal transitions out | Illegal transitions | User-visible meaning | Telemetry | Cancellation semantics | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| `UNAVAILABLE` | No recognition constructor detected at mount (and no server STT path usable) | none (re-evaluated per page load) | — | → any active state | "Voice unavailable — text fully supported" | `VOICE · UNAVAILABLE` | n/a | n/a; text path unaffected |
| `IDLE` | Voice supported, nothing active | user presses mic / speech output starts | → REQUESTING_PERMISSION, → SPEAKING | → TRANSCRIBING, → INTERRUPTING | mic idle affordance | `VOICE · AVAILABLE` | n/a | n/a |
| `REQUESTING_PERMISSION` | mic pressed, permission not yet known-granted | browser resolves permission | → LISTENING (granted), → ERROR (denied/failed) | → SPEAKING, → PROCESSING | "Waiting for microphone permission" | none (transient) | user cancel → IDLE | denial → ERROR with truthful reason, then IDLE on acknowledge |
| `LISTENING` | recognition started successfully | recognition end/stop/error | → TRANSCRIBING, → IDLE (empty/cancel), → ERROR | → SPEAKING (mic and TTS never concurrent in 3F-min) | live recording indication (mandatory, see §18) | `VOICE · LISTENING` | user stop → keep partial transcript as draft; user cancel → discard, IDLE | recognition error → ERROR |
| `TRANSCRIBING` | recognition ended with pending finalization (or audio handed to server Whisper) | final transcript resolved | → IDLE (draft ready, default), → PROCESSING (only when auto-submit explicitly enabled) | → SPEAKING | "Finalizing transcript" | `VOICE · TRANSCRIBING` | cancel discards transcript; nothing was submitted, so nothing to unwind | empty/failed → IDLE with notice, never a fabricated transcript |
| `PROCESSING` | transcript **submitted** as user message | chat response terminal (success/error) | → SPEAKING (voice output enabled+available), → IDLE | → LISTENING (mic stays closed while a voice turn is in flight, 3F-min) | existing busy/turn surface | `VOICE · PROCESSING` (mirror of turn state, not a new lifecycle) | 3F-min: none (blocking transport); 3F-full: routes to existing `interrupt_response`/`cancel_response` | provider failure → existing chat error path; → IDLE |
| `SPEAKING` | utterance actually started (`onstart`) | utterance end/cancel/error | → IDLE (completed), → INTERRUPTING, → ERROR | → LISTENING directly (must pass through INTERRUPTING or IDLE) | "speaking" indicator + always-visible stop control | `VOICE · SPEAKING` | stop control or barge-in → INTERRUPTING | synthesis error → ERROR; text already rendered, so zero information loss |
| `INTERRUPTING` | stop-speech requested while SPEAKING | `speechSynthesis.cancel()` confirmed (queue empty) | → IDLE, → LISTENING (barge-in continuation: user wants to talk) | → SPEAKING (no auto-resume of cancelled audio) | "stopped speaking" flash | `VOICE · INTERRUPTED` | idempotent; repeated requests are no-ops | if cancel fails, force state to IDLE anyway and report ERROR telemetry — UI must never stick in INTERRUPTING |
| `ERROR` | any voice-layer fault with a truthful reason | user acknowledgement or next action | → IDLE, → UNAVAILABLE (capability revoked) | → SPEAKING/PROCESSING | plain-language reason + "text mode fully supported" | `VOICE · UNAVAILABLE` or specific error detail | n/a | terminal for the attempt; never retries silently |

Global rules:

- **S1.** All transitions are driven by *real events* (recognition callbacks,
  utterance callbacks, HTTP lifecycle), never timers that fake progress.
- **S2.** Every event carries the voice-session generation id; events from a
  superseded session are dropped (§20, stale voice event). This mirrors —
  and does not replace — the 3C.1 control-epoch principle.
- **S3.** The machine is UI-layer truth about the *voice transport* only. It
  is never authoritative for conversation, generation, memory, or action
  state (FRONTEND STATE != AUTHORITATIVE STATE).

## 8. Transcription contract

- **TR1.** A transcript is untrusted user input. Recognition confidence is
  never displayed as fact, never persisted as truth, and never used to gate
  any behavior other than optionally hinting the user to re-speak.
  `speech recognition confidence != truth`.
- **TR2.** `transcript != authorization` and `transcript != confirmation`.
  A transcript that textually resembles a confirmation phrase receives
  exactly the treatment the identical typed string would receive — no more.
- **TR3.** Interim results may be displayed as visibly-provisional text but
  must never be submitted; only the final transcript may become a message.
- **TR4.** Empty/whitespace transcripts are discarded with a truthful notice;
  no empty message is created.
- **TR5.** Language: the hard-coded `en-US` must become explicit and honest —
  either configurable or labeled. No multilingual claim (deferred per
  `ZORQ-INTERACTION-MODEL-v1.md` §4).
- **TR6.** Server path (if activated in 3F): audio is uploaded only on
  explicit user action, only to the local backend, size-capped (existing
  25 MB limit), transcribed by local faster-whisper, and the temporary file
  is deleted (existing behavior). The endpoint must sit behind the same
  session/principal middleware as every other authenticated route (current
  status: middleware-global; per-endpoint assertion required in 3F tests —
  UNKNOWN until tested).
- **TR7.** The transcript inherits the privacy classification pipeline of
  typed text (`PrivacyClass`, capture policy). Nothing about voice origin
  raises or lowers sensitivity handling by itself.

## 9. Speech-output contract

- **SO1.** Voice output is strictly optional. The full response text is
  always rendered first; synthesis failure or absence loses nothing.
- **SO2.** Output lifecycle must be tracked with real events: `pending`
  (speak requested) → `speaking` (`onstart`) → terminal `completed`
  (`onend`) / `cancelled` (explicit cancel) / `error` (`onerror`) /
  `unavailable` (no `speechSynthesis`). The current fire-and-forget call is
  replaced by this tracked lifecycle.
- **SO3.** A user-visible **stop-speaking control** exists whenever state is
  SPEAKING. Keyboard accessible (§18).
- **SO4.** Synthesis is cancelled on: user stop, barge-in, navigation away
  from the conversation, component unmount, and before any new utterance.
- **SO5.** Stale speech completion: `onend` from a cancelled or superseded
  utterance must not mark a newer state as completed (session-generation
  guard, §20).
- **SO6.** iOS/Safari gesture requirements: if synthesis cannot start
  without a user gesture, the UI states this truthfully instead of
  pretending to speak.
- **SO7.** Speech output never blocks input. Typed input remains usable
  while SPEAKING.

## 10. Barge-in contract

Scenario: ZORQ is SPEAKING and the user starts speaking (or presses the mic).

- **B1.** Barge-in detection in 3F is **explicit** (mic press / stop control),
  not acoustic VAD. Open-mic-during-TTS with echo handling is out of scope
  (no echo-cancellation substrate exists in the repo; claiming acoustic
  barge-in would be untruthful).
- **B2.** The high-priority local path from `ZORQ-BARGE-IN-INTERRUPTION-v1.md`
  §1 is honored: stopping speech output is a local, immediate operation
  (`speechSynthesis.cancel()`), requiring **no LLM cycle and no network
  round-trip**.
- **B3.** Flow: `SPEAKING → INTERRUPTING → (speech output cancelled) →
  LISTENING` (mic opens for the user's utterance). The cancelled audio is
  never auto-resumed; the response **text** remains fully visible, so the
  user has lost nothing.
- **B4.** Distinctions that must never be conflated:
  - *stopping speech output* — local audio cancel; conversation state
    untouched; always what barge-in does first;
  - *pausing generation* — `pause_response` (3C.1), resumable,
    prefix preserved; 3F-full only;
  - *cancelling generation* — `cancel_response`, non-resumable; 3F-full
    only; never triggered implicitly by barge-in;
  - *creating user input* — the barge-in utterance becomes an ordinary
    transcript draft/message; it is not itself a control command;
  - *abandoning a response* — a policy outcome, only via explicit CANCEL
    under `ResponseControlState` precedence;
  - *resuming a response* — existing `resume_response` semantics
    (`ResumePolicy`, "what were you saying"), untouched by 3F.
- **B5.** In 3F-min, generation is not interruptible (blocking transport);
  barge-in during PROCESSING is limited to preparing the next input. The UI
  must not display a fake "stopped generating" affordance.
- **B6.** If the barge-in utterance is a bare control word ("stop"), it is
  handled as the *speech-output* stop it contextually is
  (`STOP(target=SPEECH)` per `ZORQ-INTERACTION-CONTROL-CONTRACT-v1.md`);
  it must never be auto-escalated to `STOP(target=ACTION)`. A generic STOP
  with unknown target fails validation — that existing rule stands.

## 11. Interruption integration

Phase 3F **reuses** the existing, implemented interruption system and adds no
second state machine:

- `ResponseControlState` (ACTIVE / STOP_REQUESTED / PAUSE_REQUESTED /
  CANCEL_REQUESTED / RESUME_REQUESTED / TERMINAL) with monotonic
  `control_epoch` and stale-producer rejection
  (`ZORQ-RESPONSE-CONTROL-PROTOCOL-v1.md`, verified by
  `tests/test_phase3c1_concurrent_interrupt.py`);
- deterministic terminal precedence CANCEL > STOP > PAUSE > completion;
- out-of-band control methods (`interrupt_response`, `pause_response`,
  `cancel_response`, `skip_response`, `resume_response`,
  `continue_response`);
- `ResponseCursor` preservation, including `speech_state` and
  `last_spoken_boundary` — which 3F finally populates with a real value
  (character offset of the last fully-spoken utterance boundary) instead of
  `text-only:no-audio-boundary`, **without changing the field contract**.

Mapping:

| Voice event | Existing mechanism used |
|---|---|
| Stop speaking (barge-in step 1) | local synthesis cancel; if 3F-full: `SpeechState.INTERRUPTED` recorded on the response cursor |
| "Stop" during generation (3F-full) | `interrupt_response` → `STOP_REQUESTED` → `GenerationState.INTERRUPTED`, prefix preserved |
| "Pause" (3F-full) | `pause_response` → resumable PAUSED |
| "What were you saying?" | existing `resume_response` natural-phrase path — unchanged |
| Concurrent controls | existing precedence + control epoch — unchanged |

Constraint: `src/zroq/conversation_runtime.py` control semantics are not
modified by 3F. If 3F-full wiring requires new *transport* (HTTP/stream)
around the runtime, that transport is additive and contract-tested against
the existing behavior.

## 12. Authorization boundary

Voice interacts with the action pipeline **only** as a source of ordinary
user input. The pipeline is unchanged:

```text
identity → session → grant → capability → confirmation
→ immutable ActionSnapshot → one-use lease → device execution
→ verification → audit
```

- **A1.** Voice cannot create identity or sessions. `identity.py` already
  enforces that voice/model cannot create sessions
  (`ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md` B-10); 3F adds a test
  asserting no voice code path touches session establishment.
- **A2.** Voice never touches grants, capabilities, leases, execution,
  verification, or audit. A voice-expressed action request enters exactly
  where a typed request enters and is subject to identical policy.
- **A3.** All master invariants apply verbatim to voice-originated turns:
  MODEL CONFIDENCE != AUTHORIZATION, INTENT != AUTHORIZATION, PLAN !=
  AUTHORIZATION, SPECIALIST OUTPUT != AUTHORIZATION, CAPABILITY DISCOVERY !=
  PERMISSION, PAST SUCCESS != AUTHORIZATION, USER HISTORY != CURRENT
  AUTHORIZATION, TOOL AVAILABILITY != AUTHORITY, PROVIDER ACCEPTANCE !=
  SUCCESS, EXECUTION != VERIFIED OUTCOME, MEMORY RETRIEVAL != MEMORY
  GOVERNANCE, PROACTIVE SUGGESTION != AUTHORIZED ACTION, EVOLUTION PROPOSAL
  != SELF-MODIFIED SECURITY; frontend: UI VISIBILITY != AUTHORIZATION, UI
  INTENT != AUTHORIZATION, DISPLAYED CAPABILITY != PERMISSION, FRONTEND
  STATE != AUTHORITATIVE STATE.
- **A4.** The GET-only facade posture (DQ-16) is unchanged: no voice code
  adds POST action surfaces.

## 13. High-assurance confirmation

- **HC1.** A spoken utterance is never a confirmation. Confirmation remains
  a digest-bound record (`confirmation.py`): bound to `action.digest()` (the
  **ActionSnapshot** digest), session, policy version, and security epoch.
  Confirmation for action A cannot approve mutated action B
  (`ZORQ-ACTION-KERNEL-IMPLEMENTATION.md` invariant 9). 3F changes none of
  this.
- **HC2.** If a future phase adds voice-*surfaced* confirmation UX, the
  confirmation act itself must remain a policy-defined method on an
  authenticated session presenting the exact snapshot digest — the voice
  transport may *display* the request, never *satisfy* it. Recognizing the
  word "yes" in audio is expressly insufficient (transcription error,
  replay, and third-party speech are indistinguishable at this layer).
- **HC3.** Digest integrity: nothing in the voice layer may hold, mutate, or
  re-serialize an ActionRequest; the snapshot/deep-freeze boundary is not
  crossed by any 3F component.
- **HC4.** Threat linkage: `ZORQ-THREAT-TEST-CATALOG.md` voice replay /
  synthesis / speaker-spoofing entries remain open threats mitigated by HC1
  (voice grants no authority to replay), not by detection claims the
  repository cannot back.

## 14. Memory integration

Voice must not alter memory governance. All behavior below is the existing
governed pipeline, entered through the transcript:

- **M1.** Spoken input can create memory exactly as typed input can — via
  the same capture policy (`MemoryCapturePolicy`, `RetentionMode`,
  `RetentionOverride`), the same continuity engine record path, the same
  provenance (`source` may truthfully record modality `voice`; modality
  never changes governance outcome).
- **M2.** "Remember this" spoken → transcript → existing
  `RuntimeCommandKind.REMEMBER_THIS` classification (canonical runtime) or
  the existing explicit capture path — identical to typing it.
- **M3.** "Don't remember this" → existing `DO_NOT_REMEMBER` /
  `EXPLICIT_DO_NOT_REMEMBER` retention path. It applies to the *content*
  policy; truthful audit/telemetry events about the interaction itself
  follow existing policy and are not silently suppressed.
- **M4.** Sensitive speech: transcripts flow through the same
  `PrivacyClass` / sensitive-gating rules
  (`ZORQ-CONTEXTUAL-MEMORY-ACTIVATION-v1.md` §sensitive gating: relevance
  alone never justifies surfacing). No voice-specific sensitivity shortcut
  exists in either direction.
- **M5.** Unsubmitted transcripts are **not** memory candidates. Only a
  submitted message enters capture policy. Interim recognition text is
  never persisted.
- **M6.** Contextual memory activation and explicit memory retrieval
  ("what do you remember…", `EXPLICIT_RECALL`, `SHOW_MEMORY`) work through
  the same classification of the transcript text — no voice-specific recall
  privilege.
- **M7.** Preserved distinctions: historical truth != current truth;
  source != derived != inference (a transcript is *source*; recognition is
  not inference authority); relevance != authority.

## 15. Privacy / data lifecycle

Grounded in `ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md` (DESIGNED) and actual code:

| Aspect | Current truth | 3F requirement |
|---|---|---|
| Microphone access | only during an explicit user-initiated recognition session; never always-on | unchanged; wake word remains forbidden in 3F |
| Browser permission | delegated to browser prompt; denial handled honestly | add pre-permission explanation UI; never nag-loop |
| Audio retention | app: none (browser path captures no audio in app code; Whisper path deletes temp file) | codify: ZORQ never persists raw audio in 3F |
| Transcript retention | persists only when submitted, under normal message/memory governance | unchanged; document it visibly |
| Provider egress | app sends no audio to any provider; transcript text goes wherever the configured chat provider already goes (`provider egress posture` field exists, `NO_EGRESS` for local contexts) | voice adds no new egress class |
| **Browser-vendor STT egress** | Web Speech recognition in Chromium-class browsers may send audio to the vendor's servers; the repository does not document or control this | **GAP-1 (must be disclosed in UI copy in 3F):** browser speech recognition may transmit audio to the browser vendor; users who require local-only processing must use the local Whisper path or text |
| Local processing | faster-whisper path is genuinely local (CPU); currently dormant | if activated, remains local-only |
| Temporary speech data | Whisper temp file deleted in `finally`; recognition interim text held in component state only | unchanged; add test for temp deletion |
| Sensitive data handling | inherited from text pipeline (`PrivacyClass`) | unchanged |
| Deletion behavior | transcripts, once messages/memories, follow existing deletion/tombstone propagation (`ZORQ-MEMORY-DELETION-IMPLEMENTATION-v1.md`) | unchanged; no separate voice deletion store exists because no voice data store exists |

Declared GAPs (no authoritative repository answer — **not** invented here):

- **GAP-1:** browser-vendor audio egress during Web Speech recognition is
  undocumented and uncontrolled.
- **GAP-2:** `ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md` contains no voice/audio
  section; a voice annex is required at 3F implementation time.
- **GAP-3:** per-endpoint auth assertion for `/api/voice/transcribe` is
  untested (middleware-global auth exists; explicit test required).
- **GAP-4:** no retention statement exists for recognition *interim* text in
  browser memory across component remounts (believed transient; needs a
  test, not a claim).

## 16. Offline / local / online model

Three distinct things, never conflated:

1. **Browser speech capability** (Web Speech API): availability is a
   browser property; recognition may itself require the *browser's* online
   service (Chromium). Therefore **browser speech recognition is not
   "offline voice."** No offline-voice claim is permitted in 3F.
2. **Local speech model** (faster-whisper): genuinely local when configured;
   currently dormant; its availability is reported truthfully per device
   (`voice_server` capability, `/api/voice/status`).
3. **Online provider:** the chat provider consuming the transcript — an
   entirely separate concern with its own existing egress posture.

Preserved invariants: connectivity != authority; device capability != user
permission (a working mic grants nothing); online availability != ZORQ
availability (text-mode ZORQ is fully functional with zero voice capability).
Full local-first voice operation belongs to Phase Z-LD.1
(`ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md` §phases), not 3F.

## 17. Telemetry

Voice state joins the existing Z-UI.1 telemetry layer (the same surface that
shows PROVIDER / MODEL / THREAD / RETRIEVAL), replacing implementation
narration in dialogue. Displayable tokens, each requiring live runtime
evidence:

| Token | Evidence required |
|---|---|
| `VOICE · AVAILABLE` | recognition constructor detected this session |
| `VOICE · LISTENING` | active recognition session (post-`start`, pre-end) |
| `VOICE · TRANSCRIBING` | recognition ended with finalization pending, or an in-flight `/api/voice/transcribe` call |
| `VOICE · PROCESSING` | submitted voice-originated turn currently in flight |
| `VOICE · SPEAKING` | utterance `onstart` fired and not yet terminal |
| `VOICE · INTERRUPTED` | explicit cancel/barge-in actually executed |
| `VOICE · UNAVAILABLE` | no recognition constructor and no usable server path |

Rules: no state may be displayed on intention alone (e.g. `SPEAKING` before
`onstart`); backend voice events (`voice.session_started/ended`) continue to
flow to the cognitive event log; the facade's `voice.state` flips from
`NOT-IMPLEMENTED` to a truthful capability descriptor **only** in the 3F
implementation phase, gated on the acceptance tests passing.

## 18. Accessibility

- Keyboard: mic toggle, stop-listening, and stop-speaking are all reachable
  and operable by keyboard; documented shortcuts; no pointer-only control.
- Screen readers: mic button carries state via `aria-pressed` (already
  present) plus `aria-label` updates; state changes announced via a polite
  live region ("Listening", "Stopped listening", "Speaking response",
  "Speech stopped"); decorative waveform stays `aria-hidden` (already true).
- Microphone status: recording indication is mandatory, visible, and not
  color-only whenever LISTENING.
- Focus: opening/closing voice never steals focus from the input; after
  barge-in, focus lands on the input field.
- Speech output indication: SPEAKING state visibly indicated with an
  adjacent stop control.
- Reduced motion: waveform/pulse animations respect the existing
  `useReducedMotion` hook (already implemented in VoiceDemo; extend to all
  3F surfaces).
- Non-voice fallback: every voice function has a text equivalent; voice is
  never the only path to any behavior. Voice remains optional, permanently.

## 19. Desktop / mobile

Truthful per-platform expectations (no parity claim — the repository
supports none beyond feature detection today):

| Platform | Recognition | Synthesis | Expectation |
|---|---|---|---|
| Desktop Chromium | usually available (vendor-server-backed; GAP-1 applies) | available | full 3F feature set |
| Desktop Firefox | typically unavailable by default | available | `UNAVAILABLE` input state, honest copy; speech **output** may still work |
| Desktop Safari | partially available (webkit prefix) | available (gesture rules) | feature-detected; no hard-coded claims |
| Mobile Chromium (Android) | usually available | available | full set; test recognition auto-end behavior on pause |
| Mobile Safari (iOS) | limited/version-dependent | gesture-gated | must pass explicit QA before any claim; synthesis only after user gesture |
| Unsupported browsers | absent | absent | `MIC N/A` + full text mode (existing behavior preserved) |

Tab backgrounding: recognition and synthesis are suspended/killed by mobile
browsers; the state machine must resolve to IDLE/ERROR truthfully on
`visibilitychange`, never remain in a phantom LISTENING/SPEAKING state.

## 20. Failure / degradation model

Fail-closed for authority, fail-open for text availability:

| Failure | Behavior |
|---|---|
| Microphone denied | ERROR with truthful reason → IDLE; text unaffected; no retry loop |
| Microphone unavailable (no device) | recognition error surfaces as ERROR; same handling |
| Recognition unavailable | UNAVAILABLE at mount; mic control replaced by honest label (existing) |
| Recognition error | ERROR; partial transcript (if any) kept as editable draft, clearly draft |
| Recognition timeout / auto-end | treat as normal end; empty → IDLE with notice |
| Empty transcript | discarded; no message created; truthful notice |
| Speech output unavailable | skip synthesis silently at capability level; text already rendered; telemetry never claims SPEAKING |
| Speech output error | ERROR telemetry; state → IDLE; text intact |
| User interrupts speech | INTERRUPTING → cancel → IDLE/LISTENING (§10) |
| Provider unavailable | existing chat error path; voice adds nothing; transcript preserved in input for retry |
| Concurrent interruption | generation side: existing 3C.1 epochs/precedence (unchanged); voice side: session-generation guard, last-user-action wins, idempotent cancels |
| Stale voice event | events tagged with voice-session generation id; stale events dropped, logged to console-free debug channel |
| Stale speech completion | `onend` of superseded utterance cannot complete a newer state (SO5) |
| Tab backgrounded | resolve to IDLE truthfully on visibility loss; no phantom states |
| Device disconnect (mic unplugged mid-listen) | browser fires recognition error → ERROR path; no crash; text unaffected |

## 21. Implementation work packages

> None of these are implemented in this phase. Order and boundaries are the
> design lock. File lists are the *expected* change surface; additions inside
> the listed directories are permitted, changes outside them are not.

### 3F.1 Voice runtime foundation (frontend state machine)
- **Purpose:** implement §7 as a single `useVoice` controller owning the
  state machine, session-generation guards, and event discipline; retire the
  boolean-level `useSpeech` surface into it.
- **Dependencies:** none (pure frontend; Z-UI.1 baseline).
- **Files:** `frontend/hooks/useVoice.ts` (new), `frontend/hooks/useSpeech.ts`
  (absorbed/deprecated), `frontend/lib/types.ts`.
- **Boundary:** no component rewiring yet; no backend changes.
- **Tests:** state-transition unit tests (legal/illegal table from §7);
  stale-event rejection tests.
- **Acceptance:** every §7 illegal transition provably unreachable.
- **Security:** none new; establishes S3 (UI state non-authoritative).

### 3F.2 Speech input
- **Purpose:** §8 transcription contract — draft semantics, interim/final
  separation, empty handling, permission UX, language honesty.
- **Dependencies:** 3F.1.
- **Files:** `frontend/hooks/useVoice.ts`, `frontend/components/Chat.tsx`,
  `frontend/components/VoiceDemo.tsx`.
- **Boundary:** transcript remains draft-until-submit; no auto-submit default.
- **Tests:** acceptance tests 2–8 (§23).
- **Acceptance:** transcript path byte-identical to typed path at submission.
- **Security:** TR1/TR2 asserted by test (voice string == typed string
  treatment).

### 3F.3 Speech output
- **Purpose:** §9 tracked synthesis lifecycle + stop control, replacing
  fire-and-forget.
- **Dependencies:** 3F.1.
- **Files:** `frontend/hooks/useSpeechOutput.ts` (new),
  `frontend/components/Chat.tsx`.
- **Boundary:** consumes final response text only; no spoken-only content.
- **Tests:** acceptance tests 11–15, 19–20.
- **Acceptance:** SO1–SO7 all evidenced; no phantom SPEAKING telemetry.
- **Security:** none new.

### 3F.4 Barge-in / interruption integration
- **Purpose:** §10/§11 — INTERRUPTING flow, local high-priority cancel,
  optional mic continuation; explicit 3F-min vs 3F-full decision executed
  here (C5 gate).
- **Dependencies:** 3F.2, 3F.3; 3F-full additionally requires a streaming
  transport work item exposing the existing control API (scoped separately
  if chosen — it touches `backend/app/main.py` and the `src/zroq` runtime
  *wiring*, never its control semantics).
- **Files:** `frontend/hooks/useVoice.ts`, `frontend/components/Chat.tsx`;
  3F-full only: `backend/app/main.py` (+ new transport module),
  `backend/app/schemas/*`.
- **Boundary:** zero new interruption state machines; zero changes to
  `ResponseControlState`/epoch semantics.
- **Tests:** acceptance tests 16–18; 3C.1 regression suite must stay green
  untouched.
- **Acceptance:** B1–B6; stopping speech never cancels generation
  implicitly.
- **Security:** control commands remain target-explicit
  (`STOP(target=SPEECH)`); no auto-escalation to ACTION.

### 3F.5 Conversation convergence
- **Purpose:** §6 C1–C4 hard-wired and tested; `interaction_mode` effect
  audit (telemetry + synthesis offer only).
- **Dependencies:** 3F.2.
- **Files:** `frontend/components/Chat.tsx`, `frontend/lib/api.ts`,
  `backend/app/main.py` (assert-only changes/tests around the chat route).
- **Boundary:** no second reasoning path may exist; diff-level review that
  no voice-conditional branches touch retrieval/governance/provider code.
- **Tests:** acceptance tests 21–23.
- **Acceptance:** identical backend traces for identical text submitted by
  voice vs keyboard (modulo the two permitted telemetry events).
- **Security:** M1–M7 asserted.

### 3F.6 Voice telemetry / UI integration
- **Purpose:** §17 tokens in the Z-UI.1 telemetry surface; facade voice
  state updated truthfully; cognitive events extended only where evidenced.
- **Dependencies:** 3F.1–3F.4.
- **Files:** `frontend/components/Chat.tsx`, `frontend/lib/types.ts`,
  `backend/app/zorq_facade.py`, `backend/app/cognition/events.py`
  (vocabulary additions only), `backend/app/main.py`.
- **Boundary:** no state displayed without runtime evidence.
- **Tests:** telemetry-truth tests: force each state, assert token; assert
  absence when evidence absent.
- **Acceptance:** the seven tokens of §17, each evidence-gated.
- **Security:** truthfulness invariant (no simulated capability).

### 3F.7 Privacy / degradation hardening
- **Purpose:** §15 GAP closure (voice annex to the privacy contract, GAP-1
  disclosure copy, transcribe-endpoint auth test, temp-file deletion test),
  §20 failure matrix, §19 backgrounding behavior.
- **Dependencies:** 3F.2, 3F.3.
- **Files:** `docs/zorq/ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md` (voice annex),
  `frontend/components/*` (disclosure copy), `tests/test_phase3f_voice_contract.py`
  (new), `backend/app/voice/transcription.py` (only if a test exposes a
  defect; none known).
- **Boundary:** no new privacy policy invented — only documenting and
  testing actual behavior.
- **Tests:** acceptance tests 1, 9–10, plus GAP-3/GAP-4 probes.
- **Acceptance:** every §15 row either evidenced or explicitly labeled GAP
  in user-facing copy.
- **Security:** egress truthfulness (GAP-1 disclosure).

### 3F.8 Verification / browser QA
- **Purpose:** full acceptance run (§23–§24): backend pytest, frontend
  typecheck/lint/build, desktop+mobile browser QA including voice routes,
  0 console errors, 0px overflow, forbidden-text scan, accessibility pass.
- **Dependencies:** all previous.
- **Files:** `tests/zui1_browser_qa.py` (extend), new
  `tests/phase3f_voice_qa.py`, `docs/zorq/ZORQ-PHASE3F-VERIFICATION.md` (new).
- **Boundary:** verification only.
- **Tests:** the 32-test plan of §23 in full.
- **Acceptance:** §24 checklist complete; no regression of the 276-test
  backend baseline or Z-UI.1 QA truth checks.
- **Security:** identity-protection scan stays clean across all new copy.

## 22. Exact files expected to change (future implementation)

New: `frontend/hooks/useVoice.ts`, `frontend/hooks/useSpeechOutput.ts`,
`tests/test_phase3f_voice_contract.py`, `tests/phase3f_voice_qa.py`,
`docs/zorq/ZORQ-PHASE3F-VERIFICATION.md`,
privacy voice annex (section inside `ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md`).

Modified: `frontend/hooks/useSpeech.ts` (absorbed), `frontend/components/Chat.tsx`,
`frontend/components/VoiceDemo.tsx`, `frontend/lib/api.ts`,
`frontend/lib/types.ts`, `backend/app/zorq_facade.py` (truthful voice state),
`backend/app/cognition/events.py` (event vocabulary only),
`backend/app/main.py` (telemetry/transport only), `tests/zui1_browser_qa.py`.

3F-full only (if C5 gate selects it): a new streaming/control transport
module under `backend/app/` plus wiring; `src/zroq/conversation_runtime.py`
control **semantics remain unmodified** in either target.

Explicitly not changed in any 3F target: `src/zroq/action_kernel.py`,
`authority.py`, `grants.py`, `capabilities.py`, `confirmation.py`,
`leases.py`, `identity.py`, `verification.py`, `audit.py`,
`device_agent.py`, memory governance modules.

## 23. Test strategy — exact acceptance tests

Frontend unit/QA tests (F), backend pytest (B), browser QA (Q):

1. **Unavailable browser** (F/Q): no recognition ctor → UNAVAILABLE, `MIC N/A`, text path fully functional, telemetry `VOICE · UNAVAILABLE`.
2. **Permission denied** (Q): `not-allowed` → ERROR with truthful copy → IDLE; no retry loop; no phantom LISTENING.
3. **Permission granted** (Q): REQUESTING_PERMISSION → LISTENING; recording indicator visible.
4. **Start listening** (F): `start()` transitions IDLE→…→LISTENING only on real recognition start; telemetry token appears.
5. **Stop listening** (F): user stop → recognition stopped, partial transcript retained as draft, state IDLE.
6. **Transcript produced** (F): final result populates draft input; interim never submittable.
7. **Empty transcript** (F): whitespace/empty → no message created, truthful notice, IDLE.
8. **Transcript cancellation** (F): cancel during LISTENING/TRANSCRIBING discards draft; nothing persisted.
9. **Provider unavailable** (B): `/api/voice/status` reports `mode:"browser"` with reason; `/api/voice/transcribe` → 503, honest detail.
10. **Provider available** (B): with faster-whisper configured, transcribe returns text; temp file deleted (GAP-4/temp probe); size cap enforced (413).
11. **Speech output unavailable** (F): no `speechSynthesis` → no SPEAKING state ever; text rendered; no error thrown.
12. **Speech output available** (F): voice-mode response with synthesis → pending→speaking on real `onstart`.
13. **Speaking** (F/Q): SPEAKING indicator + stop control present; typed input still usable.
14. **Speech completion** (F): `onend` → COMPLETED → IDLE; telemetry clears.
15. **Speech cancellation** (F): stop control → cancel → INTERRUPTING → IDLE; idempotent on double-press.
16. **Barge-in** (F/Q): mic press while SPEAKING → speech output cancelled locally (no network dependency) → LISTENING; response text intact.
17. **Concurrent interruption** (B): existing `test_phase3c1_concurrent_interrupt.py` green and untouched; 3F-full adds: STOP during stream via transport → INTERRUPTED, stale deltas rejected.
18. **Stale voice event** (F): recognition event from superseded session generation is dropped; state unchanged.
19. **Stale speech completion** (F): `onend` of cancelled utterance cannot mark newer state completed.
20. **Stop while speaking** (F): distinct from barge-in — stop → IDLE (mic does not open).
21. **Voice → conversation runtime** (B): identical message via voice mode and text mode produces identical retrieval/governance/provider traces; only `voice.session_started/ended` events differ.
22. **Voice → contextual memory behavior** (B): voice-originated message triggers the same contextual activation as typed; no extra recall privilege.
23. **Explicit memory query through voice** (B): spoken "what do you remember about X" classifies exactly as typed equivalent.
24. **Voice → action request** (B): a voice-originated action request enters the standard pipeline; no state in kernel/grants reflects modality.
25. **Voice cannot bypass authorization** (B): transcript resembling a grant/confirmation phrase produces zero authority change; existing `test_security` suite green.
26. **Voice cannot silently confirm sensitive action** (B): no code path from voice layer to `issue_confirmation`; a spoken "yes" creates no Confirmation record.
27. **ActionSnapshot binding intact** (B): `test_phase26_action_snapshot.py` green and untouched; confirmation digest validation unchanged.
28. **Verification remains required** (B): `test_phase25_execution_barrier.py` / outcome-verification tests green; voice adds no verified-outcome shortcut.
29. **Audit remains truthful** (B): voice telemetry events appear in the event log only when the underlying transition happened; no fabricated audit rows.
30. **Mobile behavior** (Q): mobile Chromium route QA — recognition lifecycle, backgrounding resolves to IDLE, 0 console errors, 0px overflow.
31. **Desktop behavior** (Q): desktop QA across supported/unsupported browsers per §19 matrix.
32. **Accessibility behavior** (Q): keyboard-only operation of mic/stop controls; live-region announcements; reduced-motion honored; recording indication non-color-dependent.

## 24. Acceptance criteria

Phase 3F implementation is accepted only when all hold:

1. All 32 tests of §23 pass on Windows verification (matching the Z-UI.1
   verification discipline).
2. Baseline non-regression: backend 276-test suite (7 skipped) still passes;
   frontend typecheck/lint/build pass; desktop+mobile browser QA 9/9 routes,
   0 console errors, 0px overflow, truth checks pass.
3. No telemetry token displays without runtime evidence (§17 audit).
4. Diff audit: no changes to authorization/confirmation/lease/execution/
   verification/audit/memory-governance modules (§22 exclusion list).
5. Identity-protection scan clean across all new UI copy, docs, tests,
   metadata.
6. GAP-1 disclosure present wherever browser recognition is offered.
7. Voice fully optional: text-only session exercises 100% of conversational
   functionality.
8. C5 decision (3F-min vs 3F-full) recorded with owner approval before
   3F.4 merges.

## 25. Known gaps

- GAP-1: browser-vendor audio egress during Web Speech recognition —
  undocumented, uncontrolled, must be disclosed (§15).
- GAP-2: privacy contract has no voice/audio annex yet.
- GAP-3: `/api/voice/transcribe` per-endpoint auth assertion untested
  (middleware-global auth observed; explicit test required).
- GAP-4: interim-transcript memory-residency behavior untested.
- GAP-5: `/api/chat` transport cannot express mid-generation control; the
  3C/3C.1 control API is implemented in `src/zroq` but not HTTP-reachable —
  this is the structural reason for the C5 gate.
- GAP-6: `useSpeech` concatenates result alternatives without
  interim/final distinction — must be corrected in 3F.2.
- GAP-7: `speechSynthesis` is not cancelled on unmount/navigation today.
- GAP-8: recognition language hard-coded `en-US`, unlabeled.
- GAP-9: no per-browser capability matrix has been empirically verified;
  §19 expectations require QA evidence before any public claim.

## 26. Explicit invariants (binding on all 3F work)

```text
VOICE CONVENIENCE            != HIGH-ASSURANCE IDENTITY
SPEECH RECOGNITION CONFIDENCE!= TRUTH
TRANSCRIPT                   != AUTHORIZATION
TRANSCRIPT                   != CONFIRMATION
MODEL CONFIDENCE             != AUTHORIZATION
INTENT                       != AUTHORIZATION
PLAN                         != AUTHORIZATION
SPECIALIST OUTPUT            != AUTHORIZATION
CAPABILITY DISCOVERY         != PERMISSION
PAST SUCCESS                 != AUTHORIZATION
USER HISTORY                 != CURRENT AUTHORIZATION
TOOL AVAILABILITY            != AUTHORITY
PROVIDER ACCEPTANCE          != SUCCESS
EXECUTION                    != VERIFIED OUTCOME
MEMORY RETRIEVAL             != MEMORY GOVERNANCE
PROACTIVE SUGGESTION         != AUTHORIZED ACTION
EVOLUTION PROPOSAL           != SELF-MODIFIED SECURITY
UI VISIBILITY                != AUTHORIZATION
UI INTENT                    != AUTHORIZATION
DISPLAYED CAPABILITY         != PERMISSION
FRONTEND STATE               != AUTHORITATIVE STATE
CONNECTIVITY                 != AUTHORITY
DEVICE CAPABILITY            != USER PERMISSION
ONLINE AVAILABILITY          != ZORQ AVAILABILITY
BROWSER SPEECH CAPABILITY    != OFFLINE VOICE
STOPPING SPEECH OUTPUT       != CANCELLING GENERATION
VOICE MODALITY               != MEMORY GOVERNANCE CHANGE
```

Plus, unchanged and untouched: the core action pipeline
(identity → session → grant → capability → confirmation → immutable
ActionSnapshot → one-use lease → device execution → verification → audit)
and the 3C.1 rule that a stale producer never overwrites a newer durable
control decision.

## 27. Implementation order

```text
3F.1 foundation → 3F.2 input → 3F.3 output
      → C5 decision gate (owner approval: 3F-min vs 3F-full)
      → 3F.4 barge-in → 3F.5 convergence → 3F.6 telemetry
      → 3F.7 privacy/degradation → 3F.8 verification
```

Rationale: the state machine must exist before either transport touches it;
input and output are independent after 3F.1 and may proceed in parallel;
barge-in requires both; convergence assertions are cheapest once the real
paths exist; telemetry can only display what prior packages evidence;
privacy hardening tests the finished surfaces; verification is last and
gates the phase.

---

*End of specification. This document is the only artifact produced for the
Phase 3F design lock; no runtime file was modified.*
