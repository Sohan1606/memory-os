# ZORQ — PHASE 3F VERIFICATION REPORT (Voice Interaction, 3F-MIN)

**Document:** ZORQ-PHASE3F-VERIFICATION.md
**Date:** 2026-09-29 (Rev 2 — correction pass)
**Status:** VERIFIED — all recorded results come from actual runs executed on this working tree.
**Rev 2 corrections:** (1) spec §10 **B6** implemented — a bare spoken "stop" in the
barge-in context is consumed as `STOP(target=SPEECH)` and never submitted to
`/api/chat`; (2) §8.5 of the privacy annex re-worded to evidence-accurate TTS
claims (no "fully local TTS" claim). All suites and browser QA re-run after
the corrections; every count below is from the Rev 2 runs.
**Design lock:** `docs/zorq/ZORQ-PHASE3F-VOICE-SPECIFICATION-v1.md`

---

## 1. Baseline

- Repository: `github.com/Sohan1606/memory-os`, branch `zroq/canonical-migration`.
- Baseline commit: **`1756552`** — "feat: complete ZORQ Z-UI.1 intelligence console".
- All Phase 3F work exists as an uncommitted working tree on top of that commit.
  **No commit was made and no push was performed** (handoff is the review ZIP, per instruction).

## 2. Summary of what was implemented

Phase 3F **3F-MIN**: truthful browser voice input (speech-to-text draft) and tracked
speech output (text-to-speech of the rendered response) for the Z-UI.1 console,
governed by a single explicit voice state machine, with **zero** change to
authorization, confirmation, memory governance, the action kernel, or the 3C/3C.1
interruption architecture. Voice input produces an **editable draft** that submits
through the exact same function and HTTP path as typed text. Speech output has a
tracked lifecycle (start/end/cancel/error evidence) with local barge-in: stopping
speech never cancels or signals response generation. Privacy annex §8 closes the
voice data-lifecycle documentation gap, including the mandatory disclosure that
browser speech recognition may send audio to the browser vendor's speech service.

## 3. Files changed / created

Modified (11 — `git diff --stat HEAD`: 305 insertions, 183 deletions):

| File | Change |
|---|---|
| `frontend/components/Chat.tsx` | Full voice wiring: mic control, interim/final draft handling, telemetry token, speech-output controls, barge-in, error surface, a11y live region, Escape cancel; Rev 2: `data-speech-stop-controls` evidence attribute + B6 screen-reader note |
| `frontend/components/VoiceDemo.tsx` | Rewritten on `useVoice`; renders STT vendor-egress disclosure on `/` |
| `frontend/hooks/useSpeech.ts` | **DELETED** (fire-and-forget legacy hook replaced by tracked lifecycle) |
| `frontend/lib/types.ts` | Re-exports `VoiceState` / `VoiceEvent` from the machine |
| `frontend/package.json` | Adds `test:voice` script |
| `frontend/.gitignore` | Adds `.voicetest/` |
| `frontend/app/system/page.tsx` | Voice note updated to truthful 3F-MIN status |
| `backend/app/zorq_facade.py` | Voice block → `PARTIALLY-IMPLEMENTED` with truthful 3F-MIN note; phase attribution |
| `backend/tests/test_zorq_facade.py` | Facade voice assertion updated to match |
| `docs/zorq/ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md` | §8 "Voice data lifecycle (Phase 3F annex)" appended; Rev 2: §8.5 re-worded to evidence-accurate TTS claims |
| `docs/zorq/qa/zui1-browser-evidence/qa-results.json` | Refreshed by this pass's Z-UI.1 regression QA run (verdict PASS) |

Created (8):

| File | Lines | Purpose |
|---|---|---|
| `frontend/lib/voiceMachine.ts` | 246 | Pure transition authority: 9 states, 19 events, `transition`/`isLegal`/`telemetryToken`; B6 classifier `isBareSpeechStopUtterance` + documented contextual boundary |
| `frontend/hooks/useVoice.ts` | 420 | Voice controller: machine + session-generation guards + recognition/synthesis wiring; B6 consumption of bare barge-in "stop" (no draft, no submission) |
| `frontend/hooks/useSpeechOutput.ts` | 151 | Tracked synthesis lifecycle (per-utterance generation, unmount cancel) |
| `frontend/tests/voiceMachine.spec.ts` | 173 | State-machine + B6-classifier contract spec (143 checks) |
| `tests/test_phase3f_voice_contract.py` | 322 | Root contract suite: voice creates no authority (real contracts, 14 tests) |
| `backend/tests/test_phase3f_voice_backend.py` | 244 | Backend suite: convergence, auth boundary, transcription route, governance parity (12 tests) |
| `tests/phase3f_voice_qa.py` | 496 | Playwright browser QA harness (5 passes, 55 checks incl. 7 B6 checks, evidence capture) |
| `docs/zorq/ZORQ-PHASE3F-VOICE-SPECIFICATION-v1.md` | 883 | The locked Phase 3F specification (carried in-tree) |

Explicitly **NOT** touched: `src/zroq/**` (identity, sessions, grants, capabilities,
confirmation, ActionSnapshot, leases, execution, verification, audit, action kernel),
conversation runtime / 3C / 3C.1 modules, memory governance, `backend/app/voice/transcription.py`
(no real defect found requiring change), `backend/app/main.py`, `backend/app/cognition/events.py`
(existing voice session events already truthful).

## 4. C5 decision — 3F-MIN (fixed by owner)

Implemented exactly as decided:

- Speech-output barge-in is handled **locally in the browser**: `STOP(target=SPEECH)` is an
  explicit user action that cancels synthesis only.
- Stopping speech **does not** implicitly cancel response generation — proven in browser QA
  ("stopping speech fired no network request and cancelled nothing") and by the absence of any
  generation-control transport.
- **No** generation-level STOP/PAUSE was added; **no** streaming `/api/chat`; **no**
  conversation-runtime rewrite; **no** change to 3C.1 semantics; **no**
  `STOP(SPEECH)→STOP(RESPONSE_GENERATION)` escalation.
- Optional mic continuation after barge-in opens a **new** recognition session on real
  evidence only (browser QA: "barge-in opened a NEW recognition session", "barge-in fired
  no generation-control request").

**B6 — bare "stop" during barge-in (Rev 2).** If the barge-in utterance is a bare
control word ("stop"), it is handled contextually as the `STOP(target=SPEECH)` it is —
consumed as a control acknowledgement, NOT submitted as a chat message.

- **Contextual boundary (normative):** the rule applies ONLY to the final transcript of
  a recognition session opened as a barge-in continuation (SPEAKING →
  STOP_SPEECH_REQUESTED → INTERRUPTING → LISTENING) whose normalized text (lowercase,
  punctuation stripped, whitespace collapsed) is exactly `"stop"` (or `"stop stop"`).
  Everywhere else — mic sessions started from IDLE/ERROR, typed text, or any non-bare
  barge-in utterance such as "stop the deployment review" — "stop" is ordinary user
  input and becomes a normal editable draft (proven in browser QA).
- **Speech-only:** the consumed control affects speech output only. It never escalates
  to `STOP(target=ACTION)`, `STOP(target=RESPONSE_GENERATION)`, PAUSE, or CANCEL of
  generation — the code path reaches no transport at all (browser QA: ZERO `/api/chat`
  posts for the control utterance); it creates no Confirmation record and touches no
  authorization/grant/capability/ActionSnapshot/lease/execution/verification/audit or
  memory-governance surface (static authority scan in the contract suite still passes).
- **No second interruption architecture:** the classifier is a pure boolean predicate in
  `voiceMachine.ts`; it adds no state, no event, no epoch, and no producer identity, and
  3C.1 semantics are unmodified (regression suite re-run green).
- **Evidence:** machine spec (23 classifier/boundary checks) + browser QA B6 sequence
  (7 checks: control consumed, count evidence attribute `data-speech-stop-controls`,
  no draft, zero submissions, machine → AVAILABLE, conversation unchanged, non-bare
  utterance still drafts normally).

## 5. Commands executed (all on this tree, Linux, Python 3.13.14, Node v20.20.2)

```
python -m pytest tests/ -q                                   # repo root
python -m pytest tests/test_phase3c1_concurrent_interrupt.py \
                 tests/test_phase3c_conversation_runtime.py -q
cd backend && python -m pytest                               # full backend suite
cd frontend && npm run typecheck && npm run lint && npm run test:voice && npm run build
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000    # backend up (demo mode)
npm run start                                                # next start :3000
python tests/phase3f_voice_qa.py                             # Phase 3F browser QA
python tests/zui1_browser_qa.py                              # Z-UI.1 regression QA
```

## 6. Test results — exact counts (actual runs)

| Suite | Result |
|---|---|
| Root suite `python -m pytest tests/ -q` | **282 passed, 8 skipped, 0 failed** (5 subtests passed; 8 skips are Windows-specific validations not applicable on Linux) |
| — of which new Phase 3F contract suite | **14 passed** (`tests/test_phase3f_voice_contract.py`) |
| — root suite excluding the new file (baseline regression) | **268 passed, 8 skipped, 0 failed** — no pre-existing test broken |
| 3C.1 + 3C regression (explicit run) | **35 passed, 0 failed** |
| Full backend suite `cd backend && python -m pytest` | **1099 passed, 24 skipped, 0 failed** (218.88s, Rev 2 re-run) |
| — of which new `tests/test_phase3f_voice_backend.py` | **12 passed** |
| Frontend `npm run typecheck` | **PASS** (exit 0) |
| Frontend `npm run lint` | **PASS** — "No ESLint warnings or errors" |
| Frontend `npm run test:voice` (state-machine + B6 contract) | **143 passed, 0 failed** |
| Frontend `npm run build` | **PASS** — 12/12 static pages generated |
| Phase 3F browser QA (`tests/phase3f_voice_qa.py`) | **55/55 checks passed — VERDICT: PASS** (incl. 7 B6 checks) |
| Z-UI.1 regression browser QA (`tests/zui1_browser_qa.py`) | **PASS** — 9 routes desktop (46/46 truth checks, 0 console errors) + 9 routes mobile (0px overflow, 0 console errors) |

No test was removed, skipped, or weakened to achieve these results. One new-test defect was
found and fixed during verification (a secured-mode helper cleared FastAPI dependency
overrides globally; the test now save/restores them) — a test-harness fix, not an assertion
weakening.

## 7. Browser QA performed

`tests/phase3f_voice_qa.py` (Playwright, Chromium headless 153) against `next start`
:3000 proxying to live uvicorn :8000 — five passes, 48 checks, all PASS:

1. **Desktop voice state machine** (1440×900, instrumented fake SpeechRecognition/Synthesis
   delivering *real handler events*): permission → LISTENING only on real `onstart`;
   interim visibly provisional and outside the input; final becomes an editable draft in the
   SAME input; empty result and cancel paths; denial → truthful ERROR; finished session's
   handlers physically detached.
2. **Voice turn / speech output**: PROCESSING observed during the in-flight turn (1.2s
   route delay); utterance text equals the rendered answer; no phantom SPEAKING; natural
   completion; stop-while-speaking and barge-in fire **zero** network requests. Rev 2:
   B6 sequence — bare spoken "stop" on the live barge-in session is consumed as
   STOP(target=SPEECH) (no draft, zero /api/chat posts, conversation unchanged), while a
   non-bare barge-in utterance still becomes a normal editable draft.
3. **Unsupported browser**: recognition APIs absent → "MIC N/A" + `VOICE · UNAVAILABLE`,
   text path fully usable.
4. **Disclosure / reduced motion**: GAP-1 vendor-egress disclosure rendered on `/`;
   no offline-voice claim anywhere; 0 console errors under `prefers-reduced-motion`.
5. **Mobile (390×844, touch)** + **accessibility**: 0px overflow; visibility loss resolves
   LISTENING → AVAILABLE; mic keyboard-operable; Escape cancels; polite live region present.

Evidence: `docs/zorq/qa/phase3f-voice-evidence/` (7 screenshots + `phase3f-voice-qa-results.json`).

## 8. Desktop and mobile

Verified on desktop viewport 1440×900 and mobile viewport 390×844 (touch enabled) in the
Phase 3F QA, and across all 9 routes at both form factors in the Z-UI.1 regression QA.
Z-UI.1 visual language (near-black base, burgundy/dark-red surfaces, vermilion accent,
cyan info accent, gray linework, instrument telemetry) is unchanged; the mic control is an
instrument-styled control, not a generic CTA, and does not visually imply authorization.

## 9. Privacy

- §8 annex appended to `ZORQ-PRIVACY-DATA-LIFECYCLE-v1.md`: mic access lifecycle, audio
  retention (none client-side beyond the recognition session), **GAP-1 vendor egress
  disclosure**, transcript retention parity with typed text, speech output, auth boundary,
  and explicit out-of-scope statements.
- **Rev 2 — TTS wording corrected (§8.5):** the annex no longer claims that no data
  leaves the device for speech output or that `speechSynthesis` is a "local TTS
  surface". It now states only what is evidenced: ZORQ does not send response text to
  any ZORQ-operated/-selected external TTS service; synthesis is delegated to the
  browser/platform `speechSynthesis` API, whose underlying voice implementation and
  data handling are controlled by the browser/OS vendor, are outside ZORQ's control,
  and are not guaranteed by ZORQ; no "fully local TTS"/"offline TTS" claim is made.
  Only the cancellation call itself is asserted to be a local operation. UI copy was
  reviewed and contains no conflicting claim.
- The UI **never claims browser speech recognition is local**: the disclosure (mic control
  title in Chat, visible text in VoiceDemo on `/`, system page note) states audio may be
  sent to the browser vendor's speech service. Verified in browser QA.
- Draft transcripts are never silently persisted: nothing is stored until the user submits,
  at which point the text follows the identical `/api/chat` path as typed input (backend
  convergence test).
- Local transcription route (`/api/voice/transcribe`): 503 truthfully when no model,
  413 oversize rejection, temp-file cleanup on success **and** failure — all covered in
  `test_phase3f_voice_backend.py` with actual runs.

## 10. Security / authorization boundary

Proven with **real contracts and real code paths** (no fakes):

- Spoken "yes" submitted as chat input creates **no** Confirmation record
  (`tests/test_phase3f_voice_contract.py` drives the real confirmation store).
- No confirmation bypass: `issue_confirmation` still binds ActionSnapshot digest + session;
  digest binding intact; verification remains mandatory; audit remains truthful.
- Voice conveys **zero** authority: interaction mode changes only telemetry and
  speech-output offering. `MemoryArbiter.AUTHORITY["voice"] == AUTHORITY["conversation"]
  (0.7) < AUTHORITY["explicit"] (0.9)` — asserted against the live class.
- `/api/voice/transcribe` sits behind the same middleware auth boundary as every route
  (secured-mode 401 test, GAP-3).
- Identical submitted text → identical backend pipeline for voice and text modes
  (convergence test compares real `/api/chat` responses and event streams; only the
  permitted `voice.session_started`/`voice.session_ended` telemetry events differ).

## 11. Interruption architecture

**Zero new interruption state machines.** The voice machine governs voice I/O states only;
it holds no generation control, no control epochs, no producer identity. Barge-in is a
local synthesis cancel + fresh recognition session. The 3C/3C.1 interruption architecture
(control epochs, stale-producer rejection, `InteractionControlCommand`) is untouched —
explicit regression run: **35 passed**.

## 12. Stale events / concurrency

- Every recognition/synthesis session carries a generation number; events from a superseded
  session are discarded before they can mutate newer state (`useVoice.ts`, `useSpeechOutput.ts`).
- Finished recognition sessions have their handlers physically detached — verified directly
  in browser QA ("finished session's handlers are detached (stale events undeliverable)").
- Stale synthesis `onend` after cancel changes nothing (browser QA check).
- Illegal transitions are **prevented in the machine** (`transition()` returns the prior
  state and records the rejected event), not merely UI-ignored — 143-check machine spec
  includes an exhaustive legality table (e.g., UNAVAILABLE accepts nothing).

## 13. Known limitations (truthful)

- Browser STT (Web Speech API) is vendor-dependent: availability, language quality, and
  audio egress are controlled by the browser vendor. Disclosed in UI and privacy annex.
- Voice input language is fixed to `en-US` and stated as such; no false multilingual claim.
- The local Whisper transcription route stays dormant (503) unless `WHISPER_MODEL` is set;
  the facade reports voice as `PARTIALLY-IMPLEMENTED` accordingly.
- Speech output uses browser `speechSynthesis`; voice quality/availability varies by
  platform, and `outputAvailable` is capability-detected, never assumed.
- Browser QA exercises recognition through an instrumented fake (headless Chromium exposes
  no real microphone); the fake delivers events through the real handler surface, and all
  state transitions consume only that evidence. Real-microphone behavior is bounded by the
  same machine.

## 14. 3F-FULL is NOT implemented

Explicitly out of scope and absent from this tree: streaming `/api/chat`, generation-level
STOP/PAUSE from voice, server-side barge-in, always-listening/wake-word, voice
authentication/speaker recognition, HTTP exposure of the 3C.1 control protocol. The system
page and facade state this truthfully.

## 15. 3C.1 is unmodified

`git diff --stat HEAD` contains no 3C/3C.1 module, no conversation-runtime file, and no
`src/zroq/**` file. The concurrent-interruption regression suite passes unchanged
(35 passed, 0 failed).

## 16. Protected identity not exposed

Public name "ZORQ" only. Case-insensitive scan of the working tree (excluding `.git`,
`node_modules`, `.next`, `__pycache__`, `.voicetest`, `backend/data`) for the protected
identity markers returns only the pre-existing policy meta-references in the master
specification and the QA scanners' own forbidden-token lists — the internal expansion
appears nowhere. `docs/zorq/internal/` does not exist in this tree and is additionally
excluded from the review ZIP. Browser QA forbidden-string checks passed on all routes.
